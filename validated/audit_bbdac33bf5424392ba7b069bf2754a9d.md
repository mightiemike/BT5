### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable and Lacks Coinbase Merkle Proof Validation, Enabling 64-Byte Transaction Forgery — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` is explicitly marked `#[deprecated]` in the contract because it is vulnerable to the 64-byte transaction Merkle proof forgery attack. The secure replacement, `verify_transaction_inclusion_v2`, adds a mandatory coinbase Merkle proof check to close this attack vector. However, `verify_transaction_inclusion` remains a `pub` function with no runtime access restriction. Any unprivileged NEAR caller can invoke it directly, bypassing the coinbase proof validation entirely and obtaining a `true` inclusion result for a forged transaction.

### Finding Description

`verify_transaction_inclusion` is annotated with `#[deprecated(since = "0.5.0", note = "Use verify_transaction_inclusion_v2 instead.")]` and `#[pause]`. [1](#0-0) 

In Rust, `#[deprecated]` is a **compile-time lint warning only**. It does not prevent the function from being called at runtime by an external NEAR caller. The `#[pause]` attribute only blocks calls when a `PauseManager` has explicitly paused the contract; in normal operation the contract is not paused, so the function is fully reachable. [2](#0-1) 

The function verifies a Merkle proof without any coinbase anchor check: [3](#0-2) 

The contract's own documentation warns of the consequence:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [4](#0-3) 

`verify_transaction_inclusion_v2` closes this gap by first verifying a coinbase Merkle proof against the block's `merkle_root`, which prevents an attacker from supplying a crafted 64-byte internal node as a `tx_id`: [5](#0-4) 

Because the v1 function is still publicly callable, the v2 fix is entirely bypassable.

### Impact Explanation

Any downstream NEAR contract or off-chain application that calls `verify_transaction_inclusion` to gate a Bitcoin-conditional action (e.g., releasing funds, minting tokens, confirming a cross-chain swap) can be deceived. An attacker supplies a crafted `tx_id` equal to a 64-byte internal Merkle tree node; the function computes a valid Merkle root match and returns `true`, falsely asserting that a non-existent Bitcoin transaction was confirmed. The broken invariant is: *a `true` return from `verify_transaction_inclusion` must mean the supplied `tx_id` corresponds to a real, confirmed Bitcoin transaction*. That invariant does not hold.

### Likelihood Explanation

The attack requires no privileged role, no leaked key, and no social engineering. Any NEAR account can call `verify_transaction_inclusion` directly with a crafted `ProofArgs`. The 64-byte Merkle forgery technique is well-documented (referenced in the contract itself via the BitMEX blog post). The only precondition is that the attacker knows a valid `tx_block_blockhash` on the current main chain, which is public information.

### Recommendation

Remove the public accessibility of `verify_transaction_inclusion` so it cannot be called externally. The simplest options are:

1. Change the function visibility from `pub` to `pub(crate)` so it is only callable internally (by `verify_transaction_inclusion_v2`).
2. Alternatively, add a runtime guard at the top of `verify_transaction_inclusion` that panics when called directly (i.e., not via an internal Rust call path), though option 1 is cleaner.

The `#[deprecated]` attribute alone provides zero runtime protection on NEAR.

### Proof of Concept

1. Identify any valid `tx_block_blockhash` on the current main chain (public via `get_block_hash_by_height`).
2. Retrieve the block's `merkle_root` from the stored header.
3. Construct a `tx_id` that is a 64-byte value whose double-SHA256 hash, when combined with a crafted single-element `merkle_proof`, produces the known `merkle_root`. (This is the standard CVE-2017-12842 / BitMEX 64-byte forgery technique.)
4. Call `verify_transaction_inclusion` directly with `tx_id = <forged_node>`, `tx_block_blockhash = <valid_hash>`, `tx_index = <chosen_index>`, `merkle_proof = [<crafted_sibling>]`, `confirmations = 0`.
5. The function returns `true` for a transaction that does not exist on the Bitcoin blockchain.

Calling `verify_transaction_inclusion_v2` with the same inputs would panic at the coinbase proof check, confirming that the v1 path is the sole vulnerable entry point. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L263-323)
```rust
    /// Verifies that a transaction is included in a block at a given block height
    ///
    /// # Deprecated
    /// Use [`verify_transaction_inclusion_v2`] instead, which includes coinbase merkle proof validation
    /// to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
    /// @param `tx_id` transaction identifier
    /// @param `tx_block_blockhash` block hash at which transacton is supposedly included
    /// @param `tx_index` index of transaction in the block's tx merkle tree
    /// @param `merkle_proof` merkle tree path (concatenated LE sha256 hashes) (does not contain initial `transaction_hash` and `merkle_root`)
    /// @param confirmations how many confirmed blocks we want to have before the transaction is valid
    /// @return True if `tx_id` is at the claimed position in the block at the given blockhash, False otherwise
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
    /// Multiple cases
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
