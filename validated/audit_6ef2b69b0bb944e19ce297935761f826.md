### Title
`verify_transaction_inclusion` / `verify_transaction_inclusion_v2` accept stale chain state without tip-freshness check — (File: `contract/src/lib.rs`)

---

### Summary

Both public transaction-verification entry points check confirmations against the stored `mainchain_tip_blockhash` without ever verifying that the stored tip is temporally current. If the relayer stops submitting headers, the contract's canonical chain view silently ages while `verify_transaction_inclusion` continues to return `true` for transactions that appear confirmed in the stale state but have since been reorganized out of the real chain.

---

### Finding Description

`verify_transaction_inclusion` (lines 288–323) and `verify_transaction_inclusion_v2` (lines 347–369) in `contract/src/lib.rs` both resolve the chain tip through `self.mainchain_tip_blockhash` and measure confirmations as:

```
heaviest_block_header.block_height − target_block_height + 1 >= args.confirmations
```

Neither function reads `env::block_timestamp_ms()` nor compares the stored tip's `block_header.time` against the current NEAR wall-clock time. The tip timestamp is fully accessible — `ExtendedHeader.block_header.time` is stored in `headers_pool` — but the verification path never touches it.

Contrast this with `check_pow` in `contract/src/bitcoin.rs` (lines 34–39), which **does** compare `env::block_timestamp_ms()` against the submitted header's timestamp to reject future-dated blocks. No symmetric "too-old tip" guard exists on the read path.

The relayer is the sole mechanism that advances the stored chain state. If it halts — for any reason — the contract's canonical view freezes while real Bitcoin blocks continue to accumulate, including potential reorgs.

---

### Impact Explanation

Any downstream NEAR contract or user that calls `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` to gate a fund-release decision receives a `true` result that reflects a frozen, potentially invalid chain snapshot. A transaction confirmed in the stale snapshot but subsequently reorganized out of the real chain will still pass verification. The downstream contract has no way to detect this because the light client exposes no staleness signal on the verification path — only `get_last_block_header` (lines 200–204), which callers must query separately and interpret themselves.

Corrupted proof result: `verify_transaction_inclusion` returns `true` for a transaction whose containing block no longer exists on the canonical Bitcoin chain.

---

### Likelihood Explanation

The relayer is an off-chain service with no on-chain liveness guarantee. It can halt due to infrastructure failure, misconfiguration, or deliberate disruption. For chains with higher natural reorg rates (Dogecoin, Litecoin — both supported via feature flags), even a short relayer outage combined with a shallow reorg is sufficient to trigger the condition. For Bitcoin the reorg depth required is larger, but the window of exposure grows linearly with relayer downtime. No privileged access is required to exploit the stale state once it exists; any NEAR account can call the verification functions.

---

### Recommendation

Inside both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2`, after fetching `heaviest_block_header`, add a freshness guard analogous to the one already present in `check_pow`:

```rust
let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap();
require!(
    current_timestamp <= heaviest_block_header.block_header.time + MAX_TIP_STALENESS_SECONDS,
    "light-client-stale: chain tip is too old to trust"
);
```

`MAX_TIP_STALENESS_SECONDS` should be chosen conservatively (e.g., 2–3× the expected block interval for the target chain). This mirrors the Chainlink recommendation of checking sequencer uptime before consuming feed data.

---

### Proof of Concept

1. The relayer stops submitting headers; `mainchain_tip_blockhash` freezes at height *H* with timestamp *T*.
2. The real Bitcoin/Dogecoin/Litecoin chain advances; a shallow reorg reorganizes block *B* (height ≤ *H*) out of the canonical chain.
3. An attacker (or any caller) invokes `verify_transaction_inclusion` with `tx_block_blockhash = B` and a valid Merkle proof for a transaction in *B*.
4. The contract finds *B* in `mainchain_header_to_height` (it was never removed because the reorg was never relayed), computes `heaviest.block_height − B.height + 1 >= confirmations`, and returns `true`.
5. A downstream NEAR contract that gates a fund release on this boolean transfers assets to the attacker.
6. The transaction no longer exists on the real chain; the funds are lost. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contract/src/lib.rs (L200-204)
```rust
    pub fn get_last_block_header(&self) -> ExtendedHeader {
        self.headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
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
