### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing 64-Byte Transaction Forgery Protection — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` (v1) is still a live, publicly callable NEAR entry point despite being deprecated. The coinbase Merkle proof check — added specifically in `verify_transaction_inclusion_v2` to prevent the 64-byte transaction Merkle forgery attack — is absent from v1. Any unprivileged NEAR caller can invoke v1 directly, bypassing the protection entirely and obtaining a `true` SPV proof result for a non-existent transaction.

### Finding Description

`verify_transaction_inclusion_v2` was introduced to close the 64-byte transaction Merkle forgery vulnerability (described at https://www.bitmex.com/blog/64-Byte-Transactions). It does so by requiring a separate coinbase Merkle proof that must also resolve to the same Merkle root, making it impossible to substitute an internal tree node for a leaf transaction hash. [1](#0-0) 

The coinbase proof check is **only** present in v2. The original `verify_transaction_inclusion` (v1) contains no such check: [2](#0-1) 

Critically, v1 is still decorated with `#[pause]` and remains a fully public NEAR method. The `#[deprecated]` attribute is a Rust compiler hint — it does not remove the function from the compiled WASM ABI or prevent external callers from invoking it. Any NEAR account can call `verify_transaction_inclusion` directly, skipping the coinbase proof requirement entirely.

The contract's own inline warning acknowledges the risk: [3](#0-2) 

This is a direct cross-module desynchronization: the security invariant (coinbase proof must be validated before accepting an SPV result) is enforced in `verify_transaction_inclusion_v2` but not in `verify_transaction_inclusion`, and both entry points are reachable by an unprivileged caller.

### Impact Explanation

A downstream contract or application that calls `verify_transaction_inclusion` (v1) — whether intentionally or because it was integrated before v2 existed — receives a `true` result for a crafted `tx_id` that is actually an internal Merkle tree node, not a real transaction. This allows an attacker to prove inclusion of a Bitcoin transaction that never occurred, which can be used to trigger fraudulent cross-chain asset releases or state transitions in any contract that trusts the SPV result.

### Likelihood Explanation

The entry point requires no privileged role, no staking, and no special NEAR account. Any caller who knows the block hash and can construct a valid 64-byte internal-node preimage can exploit this. The 64-byte forgery technique is publicly documented and tooled. Integrators who adopted the contract before v2 was released are especially likely to still be calling v1.

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the public ABI entirely, or add the same coinbase Merkle proof validation that exists in v2. The simplest safe fix is to make v1 private (or `#[private]`) so it can only be called internally by v2, which already does so: [4](#0-3) 

Alternatively, add a `require!(false, "use verify_transaction_inclusion_v2")` guard at the top of v1 to hard-block direct external calls.

### Proof of Concept

1. Identify a Bitcoin block `B` whose Merkle tree has at least two transactions, so that an internal node `N` (32 bytes) exists whose concatenation with itself or a sibling produces a 64-byte value that hashes to a known internal node hash `H`.
2. Construct `ProofArgs { tx_id: H, tx_block_blockhash: B.hash, tx_index: <crafted>, merkle_proof: <crafted path>, confirmations: 1 }`.
3. Call `verify_transaction_inclusion(args)` directly on the NEAR contract (not v2).
4. The function skips the coinbase proof check, computes `compute_root_from_merkle_proof(H, index, path)`, and if the crafted path resolves to `B.merkle_root`, returns `true` — falsely asserting that transaction `H` is included in block `B`. [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L276-281)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
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
