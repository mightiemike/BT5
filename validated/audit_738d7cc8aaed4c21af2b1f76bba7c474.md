### Title
Stale Chain Tip Not Detected in `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` return a `bool` result based solely on the stored chain state, with no check on whether the light client's chain tip is recent. A consumer DApp has no way to determine if the stored chain state is current. This is a direct structural analog to the Chainlink `latestAnswer` issue: the function returns a verification result without any freshness metadata, and the contract possesses all the data needed to perform a staleness check but never uses it in the verification path.

---

### Finding Description

Both public verification entry points return a bare `bool`: [1](#0-0) [2](#0-1) 

The confirmation check inside `verify_transaction_inclusion` computes depth relative to the stored `mainchain_tip_blockhash`: [3](#0-2) 

If the relayer has stopped submitting blocks, `mainchain_tip_blockhash` may point to a block that is hours, days, or weeks old. The `confirmations` argument is validated only against the stored tip height — not against real time. A caller requesting `confirmations = 6` receives a `true` result even if the stored tip is 500 blocks behind the real Bitcoin chain and the target transaction has since been reorganized out.

The Bitcoin timestamp of the chain tip is stored and accessible at runtime. `ExtendedHeader` carries `block_header.time` (a `u32` Unix timestamp): [4](#0-3) 

The contract already uses `env::block_timestamp_ms()` for freshness enforcement during block submission in `check_pow`: [5](#0-4) 

The same mechanism is used in `dogecoin.rs` and `zcash.rs`: [6](#0-5) [7](#0-6) 

However, neither `verify_transaction_inclusion` nor `verify_transaction_inclusion_v2` performs any equivalent check on the age of the stored chain tip before returning its result. The contract has the data (`mainchain_tip_blockhash` → `headers_pool` → `block_header.time`) and the clock (`env::block_timestamp_ms()`) but never compares them in the verification path.

---

### Impact Explanation

A NEAR DApp that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` to gate a payment or asset release can be deceived into accepting a Bitcoin transaction that has since been reorganized out of the real chain. The attacker exploits the gap between the light client's stale view and the actual Bitcoin chain state. The DApp receives `true` with no signal that the underlying data is stale, and has no on-chain mechanism to detect this condition. Financial loss to the DApp or its users is the direct consequence.

---

### Likelihood Explanation

The trigger condition — the relayer ceasing to submit blocks — is a realistic operational scenario: relayer downtime, network partition, economic attack on the relayer, or deliberate griefing. Once the light client is stale, any attacker who observes the gap can exploit DApps that rely on the contract's verification output. The deprecated `verify_transaction_inclusion` remains callable alongside the current v2 endpoint, doubling the exposed surface. [8](#0-7) 

---

### Recommendation

In both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2`, retrieve the chain tip's stored Bitcoin timestamp and compare it against `env::block_timestamp_ms() / 1000`. Revert if the difference exceeds a configurable staleness threshold (e.g., 7200 seconds for Bitcoin, matching the `MAX_FUTURE_BLOCK_TIME_LOCAL` constant already used in `check_pow`). Expose a view function that returns the chain tip height, its stored timestamp, and the current NEAR block time so that consumer DApps can perform their own freshness assessment before acting on a verification result.

---

### Proof of Concept

1. Light client is initialized and synced to Bitcoin mainchain tip at height N (timestamp T₀).
2. The relayer stops. No new headers are submitted. The contract's `mainchain_tip_blockhash` remains frozen at height N.
3. The real Bitcoin chain advances to height N+100. A reorg at height N-3 reverses transaction `tx_id`.
4. Attacker calls `verify_transaction_inclusion_v2` supplying the Merkle proof for `tx_id` in the block at height N-5, with `confirmations = 6`.
5. The contract computes: `(N - (N-5)) + 1 = 6 >= 6` — confirmation check passes.
6. The Merkle proof is valid against the stored block's `merkle_root` — proof check passes.
7. The function returns `true`.
8. The consumer DApp releases funds. The transaction `tx_id` does not exist on the real Bitcoin chain.

The contract never compares the stored tip timestamp T₀ against the current NEAR block time, so the staleness is invisible to both the contract and the consumer DApp. [9](#0-8)

### Citations

**File:** contract/src/lib.rs (L283-323)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
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

**File:** btc-types/src/header.rs (L25-37)
```rust
pub struct ExtendedHeader {
    pub block_header: LightHeader,
    /// Below, state contains additional fields not presented in the standard blockchain header
    /// those fields are used to represent additional information required for fork management
    /// and other utility functionality
    ///
    /// Current `block_hash`
    pub block_hash: H256,
    /// Accumulated chainwork at this position for this block
    pub chain_work: Work,
    /// Block height in the Bitcoin network
    pub block_height: u64,
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

**File:** contract/src/dogecoin.rs (L42-46)
```rust
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap();
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp too far in the future"
        );
```

**File:** contract/src/zcash.rs (L48-52)
```rust
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap(); // Convert to seconds
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp is too far ahead of local time"
        );
```
