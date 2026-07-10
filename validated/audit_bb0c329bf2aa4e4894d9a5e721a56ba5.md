### Title
No Chain-Tip Staleness Check in `verify_transaction_inclusion` Allows False Verification Against Reorganized Bitcoin State — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` verify Bitcoin transaction inclusion against the contract's stored chain tip without any check on whether that tip is current. If the off-chain relayer stops submitting blocks, the contract's chain state silently becomes stale. Any Bitcoin reorganization that occurs after the relayer halts is invisible to the contract, and the verification functions will continue returning `true` for transactions that no longer exist in the canonical Bitcoin chain.

---

### Finding Description

The contract stores the canonical Bitcoin chain tip in `mainchain_tip_blockhash` and the associated `ExtendedHeader` (which carries `block_header.time`, `block_height`, and `chain_work`). Both verification entry points read this tip to compute confirmation depth: [1](#0-0) 

Neither function checks whether the stored tip's timestamp is within an acceptable window of the current NEAR block time before proceeding. The contract has no concept of "the relayer may be down."

Block submission is gated by the `#[trusted_relayer]` macro: [2](#0-1) 

This means only the designated trusted relayer account can call `submit_blocks`. If that relayer stops for any reason — bugs, network partition, key rotation, or deliberate shutdown — no other party can advance the chain tip. The contract's stored tip freezes at the last submitted height while the real Bitcoin chain continues to grow and potentially reorganize.

The `MAX_FUTURE_BLOCK_TIME_LOCAL` constant (2 hours) is used only during *submission* to reject headers with timestamps too far ahead of NEAR's clock: [3](#0-2) [4](#0-3) 

This guard is entirely absent from the *verification* path. The same `block_header.time` field that was validated at submission time is never re-checked for staleness when `verify_transaction_inclusion` is called later.

---

### Impact Explanation

Any protocol that consumes `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` as a trust anchor — a bridge, a payment gateway, a cross-chain settlement layer — relies on the invariant that a `true` result means the transaction is in the current canonical Bitcoin chain with the required depth. That invariant breaks silently when the relayer halts:

1. The contract's tip is frozen at height H with timestamp T.
2. Bitcoin reorganizes blocks at heights H−k … H, replacing them with a different chain segment.
3. A transaction `tx` that was in the old segment at height H−k is no longer in the canonical chain.
4. An unprivileged caller submits a Merkle proof for `tx` against the block hash at H−k.
5. `mainchain_header_to_height` still maps that block hash to height H−k (the reorg was never submitted), so the block is still considered mainchain.
6. The confirmation check passes: `H − (H−k) + 1 = k+1 ≥ confirmations`.
7. The Merkle proof is valid against the stored `merkle_root`.
8. The function returns `true` for a transaction that does not exist in the real Bitcoin chain. [5](#0-4) 

The same logic applies to `verify_transaction_inclusion_v2`, which delegates to the deprecated function after the coinbase proof check: [6](#0-5) 

---

### Likelihood Explanation

The trusted-relayer restriction makes relayer downtime a realistic operational condition, not a theoretical one. A single relayer process going offline — due to infrastructure failure, a software bug, or a key rotation that leaves a gap — is sufficient. No privileged attacker action is required; the attacker only needs to observe that the contract's tip has not advanced and then submit a proof for a transaction in a reorganized block. Bitcoin reorganizations of 1–6 blocks occur regularly on mainnet; deeper reorgs are rarer but have occurred historically. The window of exploitability grows with every block the relayer misses.

---

### Recommendation

Add a staleness guard at the top of both verification functions. The stored tip's `block_header.time` is a `u32` Unix timestamp that can be compared directly against `env::block_timestamp_ms() / 1000`:

```rust
let tip = self.headers_pool
    .get(&self.mainchain_tip_blockhash)
    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap();
require!(
    current_timestamp.saturating_sub(tip.block_header.time) <= MAX_CHAIN_TIP_AGE_SECS,
    "chain tip is stale: relayer may be down"
);
```

`MAX_CHAIN_TIP_AGE_SECS` should be set conservatively (e.g., 7200 for Bitcoin's 2-hour future-block tolerance, or a governance-configurable value). This mirrors exactly the check that Bitcoin and Litecoin nodes apply to incoming headers: [7](#0-6) 

Apply the same guard in `verify_transaction_inclusion_v2`. Additionally, consider exposing a `get_chain_tip_age` view function so downstream consumers can independently monitor staleness before submitting proofs.

---

### Proof of Concept

1. Deploy the contract (Bitcoin feature) with a trusted relayer. Relayer submits blocks up to height H; tip timestamp is T.
2. Stop the relayer. No further `submit_blocks` calls are made.
3. On the real Bitcoin network, a reorganization occurs: blocks H−2, H−1, H are replaced by a new chain segment. Transaction `tx` (previously confirmed at H−2) is no longer in the canonical chain.
4. Construct a valid Merkle proof for `tx` against the block hash at H−2 (still stored in `headers_pool` and `mainchain_header_to_height` because the reorg was never submitted).
5. Call `verify_transaction_inclusion` with `tx_block_blockhash = hash(H−2)`, `confirmations = 3`, and the Merkle proof.
6. The contract checks: `H − (H−2) + 1 = 3 ≥ 3` ✓. Merkle proof validates against stored `merkle_root` ✓. Returns `true`.
7. Any bridge or settlement contract that trusted this result has now accepted a transaction that does not exist in the canonical Bitcoin chain. [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L168-179)
```rust
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
```

**File:** contract/src/lib.rs (L288-323)
```rust
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

**File:** contract/src/lib.rs (L347-369)
```rust
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );

        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** contract/src/bitcoin.rs (L34-39)
```rust
        // Check timestamp
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap(); // Convert to seconds
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp too far in the future"
        );
```

**File:** btc-types/src/network.rs (L17-17)
```rust
pub const MAX_FUTURE_BLOCK_TIME_LOCAL: u32 = 2 * 60 * 60;
```
