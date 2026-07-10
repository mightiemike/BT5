### Title
SPV Verification Operates on Stale Chain State When Relayer Stops - (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` perform confirmation counting and Merkle proof validation against the stored `mainchain_tip_blockhash` with no check that the light client's chain state is recent. If the relayer stops submitting headers, the contract's tip freezes in place and both verification entry points continue returning results as if the chain were live. This is the direct analog of M-31: an active-state check that does not verify whether the underlying data feed is still synchronized.

---

### Finding Description

The contract stores `mainchain_tip_blockhash` as its canonical chain tip. This value is updated only inside `submit_blocks`, which is gated behind `#[trusted_relayer]`. No timestamp or block-height freshness marker is recorded anywhere in the contract state when a batch is accepted.

`verify_transaction_inclusion` (and its v2 wrapper) reads the tip unconditionally:

```rust
let heaviest_block_header = self
    .headers_pool
    .get(&self.mainchain_tip_blockhash)
    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
``` [1](#0-0) 

It then counts confirmations relative to that frozen tip:

```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [2](#0-1) 

There is no guard of the form `last_submission_time + STALENESS_THRESHOLD >= env::block_timestamp()` anywhere in either verification path. The contract state contains no `last_submission_timestamp` field. [3](#0-2) 

The `#[pause]` decorator on both verification methods allows a privileged `PauseManager` to halt them manually, but this is a reactive, human-in-the-loop control that does not fire automatically when the relayer goes silent. [4](#0-3) 

---

### Impact Explanation

A downstream consumer contract (bridge, atomic-swap settlement, cross-chain DeFi) calls `verify_transaction_inclusion` and receives `true` for a transaction that was present in the light client's stale chain but has since been reorganized out of the real Bitcoin (or Litecoin/Dogecoin) network. The consumer has no on-chain signal that the verification result is based on a frozen tip; it acts on the result and releases funds or settles a position for a Bitcoin-side transaction that no longer exists.

The worst-case impact is identical in structure to M-31: the verification surface can be drained by an attacker who controls the reorg and times the proof submission to the stale window.

---

### Likelihood Explanation

- The trusted-relayer model means a single relayer outage (crash, censorship, economic exit) is sufficient to freeze the tip. No attacker capability is required for this precondition.
- Bitcoin mainnet reorgs of 1–2 blocks occur several times per year. Litecoin and Dogecoin, both supported via feature flags in this codebase, experience shallower difficulty and more frequent reorgs. [5](#0-4) 

- A bridge that sets `confirmations = 3` or `confirmations = 6` is still vulnerable if the reorg depth matches or exceeds the confirmation window while the tip is stale.
- The attacker does not need to break any cryptographic primitive; they only need a valid Merkle proof for a block that is already stored in the frozen chain.

---

### Recommendation

1. **Record a submission timestamp.** Add a `last_submission_timestamp_ms: u64` field to `BtcLightClient` and write `env::block_timestamp_ms()` into it at the end of every successful `submit_blocks` call.

2. **Add a staleness guard to both verification entry points.** Before counting confirmations, assert:

   ```rust
   require!(
       env::block_timestamp_ms() <= self.last_submission_timestamp_ms + STALENESS_THRESHOLD_MS,
       "Light client chain state is stale; verification disabled"
   );
   ```

   `STALENESS_THRESHOLD_MS` should be configurable at init time and reflect the expected relayer cadence (e.g., 2–4 hours for Bitcoin mainnet).

3. **Expose a view method** (`is_chain_fresh() -> bool`) so downstream consumers can query staleness independently before acting on a verification result.

---

### Proof of Concept

1. Bridge contract is deployed; it calls `verify_transaction_inclusion` to confirm Bitcoin deposits before releasing wrapped tokens.
2. Attacker sends 1 BTC to the bridge's deposit address. The transaction lands in block **H**. The relayer submits block **H** to the light client.
3. Attacker bribes or waits for the trusted relayer to go offline. The light client tip freezes at height **H** (or shortly after).
4. The real Bitcoin network produces a 2-block reorg. Block **H** is orphaned; the attacker's deposit transaction is no longer confirmed on the canonical chain.
5. Attacker calls the bridge's claim function, which internally calls `verify_transaction_inclusion` with `tx_block_blockhash = H`, `confirmations = 1`, and a valid Merkle proof (the proof is valid because block **H** is still stored in the frozen light client).
6. `verify_transaction_inclusion` reads the stale tip, finds `tip_height - H + 1 >= 1`, verifies the Merkle proof against the stored `merkle_root` of block **H**, and returns `true`. [6](#0-5) 

7. The bridge releases wrapped BTC to the attacker. The attacker's original Bitcoin deposit no longer exists on the canonical chain. Net result: the bridge is drained by the difference.

### Citations

**File:** contract/src/lib.rs (L96-118)
```rust
pub struct BtcLightClient {
    // A pair of lookup maps that allows to find header by height and height by header
    mainchain_height_to_header: LookupMap<u64, H256>,
    mainchain_header_to_height: LookupMap<H256, u64>,

    // Block with the highest chainWork, i.e., blockchain tip, you can find latest height inside of it
    mainchain_tip_blockhash: H256,

    // The oldest block in main chain we store
    mainchain_initial_blockhash: H256,

    // Mapping of block hashes to block headers (ALL ever submitted, i.e., incl. forks)
    headers_pool: LookupMap<H256, ExtendedHeader>,

    // If we should run all the block checks or not
    skip_pow_verification: bool,

    // GC threshold - how many blocks we would like to store in memory, and GC the older ones
    gc_threshold: u64,

    // Network type Mainnet/Testnet
    network: Network,
}
```

**File:** contract/src/lib.rs (L166-198)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
        let amount = env::attached_deposit();
        let initial_storage = env::storage_usage();
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());

        require!(
            amount >= required_deposit,
            format!("Required deposit {}", required_deposit)
        );

        let refund = amount.saturating_sub(required_deposit);
        if refund > NearToken::from_near(0) {
            Promise::new(env::predecessor_account_id())
                .transfer(refund)
                .into()
        } else {
            PromiseOrValue::Value(())
        }
    }
```

**File:** contract/src/lib.rs (L287-323)
```rust
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```
