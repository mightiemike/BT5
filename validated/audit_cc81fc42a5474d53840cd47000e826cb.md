### Title
Coinbase Merkle Proof Bypass via Direct Call to Deprecated `verify_transaction_inclusion` — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion_v2` was introduced to mitigate the 64-byte transaction Merkle proof forgery vulnerability by requiring a coinbase proof at the same depth. However, the original `verify_transaction_inclusion` function remains publicly callable and contains no such check. Any unprivileged NEAR caller can bypass the coinbase guard entirely by calling the deprecated function directly.

### Finding Description

The contract documents the 64-byte transaction Merkle proof forgery risk (https://www.bitmex.com/blog/64-Byte-Transactions) and introduces `verify_transaction_inclusion_v2` to address it. The v2 function enforces a coinbase merkle proof check before delegating to the v1 logic: [1](#0-0) 

The coinbase guard is: [2](#0-1) 

After this check passes, v2 calls `self.verify_transaction_inclusion(args.into())`.

The original function, however, is still a fully public `#[pause]`-gated method with no coinbase check: [3](#0-2) 

The `#[deprecated]` attribute is a Rust compiler hint only — it does not restrict on-chain callability. Any NEAR account can invoke `verify_transaction_inclusion` directly via a transaction, skipping the coinbase proof requirement entirely.

The v1 function only verifies that the supplied `tx_id` hashes up to the block's `merkle_root`: [4](#0-3) 

The contract's own warning acknowledges this: [5](#0-4) 

### Impact Explanation

An attacker can supply a `tx_id` that is actually an internal 64-byte Merkle tree node rather than a real transaction hash. Without the coinbase check, `verify_transaction_inclusion` will return `true` for a transaction that does not exist in the block. Any downstream contract or application that relies on this function's return value to authorize a cross-chain action (e.g., releasing funds, minting tokens) will be deceived into accepting a forged proof. The corrupted value is the proof result (`bool`) returned to the caller.

### Likelihood Explanation

The entry path requires no privileges: any NEAR account can call `verify_transaction_inclusion` with a crafted `ProofArgs`. The 64-byte forgery technique is well-documented and the necessary crafted inputs can be constructed offline. The only prerequisite is that a valid block header for the target block has already been submitted to the contract, which is the normal operating state.

### Recommendation

Remove the `pub` visibility from `verify_transaction_inclusion` or gate it with an access-control role so it cannot be called externally. Alternatively, add the same coinbase merkle proof check to `verify_transaction_inclusion` itself, making the protection unconditional regardless of which entry point is used. The safest fix is to make the deprecated function private or to have it internally call the v2 path.

### Proof of Concept

1. A relayer submits a valid block header for block `B` containing transactions `[T0_coinbase, T1, T2, T3]`. The Merkle root `R` is stored in `headers_pool`.
2. An attacker identifies an internal 64-byte Merkle node `N` in block `B`'s Merkle tree such that `compute_root_from_merkle_proof(N, idx, proof) == R`.
3. The attacker calls `verify_transaction_inclusion` directly (not v2) with:
   - `tx_id = N` (the forged internal node)
   - `tx_block_blockhash` = hash of block `B`
   - `tx_index` and `merkle_proof` crafted so the root reconstructs to `R`
   - `confirmations` = any valid value
4. The function returns `true`.
5. Any recipient contract that calls `verify_transaction_inclusion` and acts on a `true` result is deceived into treating the non-existent transaction as confirmed. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

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

**File:** contract/src/lib.rs (L347-368)
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
```
