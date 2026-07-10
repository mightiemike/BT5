### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling Merkle Proof Forgery — (`contract/src/lib.rs`)

### Summary
The contract exposes two transaction-inclusion verification endpoints. The original `verify_transaction_inclusion` was deprecated in v0.5.0 and replaced by `verify_transaction_inclusion_v2`, which adds a mandatory coinbase Merkle proof check specifically to close the 64-byte transaction Merkle proof forgery vulnerability. However, the deprecated function is still a live, public, unpermissioned contract method. Any NEAR caller can invoke it directly, bypassing the coinbase proof guard entirely and obtaining a `true` result for a fabricated transaction inclusion claim.

### Finding Description
`verify_transaction_inclusion` is annotated `#[deprecated]` and carries an explicit warning that it "may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [1](#0-0) 

Despite the deprecation, the function remains a `pub` method on the contract with only a `#[pause]` guard — no role restriction, no access control. [2](#0-1) 

The replacement, `verify_transaction_inclusion_v2`, first validates a coinbase Merkle proof against the block's `merkle_root` before delegating to the deprecated function: [3](#0-2) 

Because the deprecated path is still reachable directly, the coinbase guard is trivially skipped. The underlying `compute_root_from_merkle_proof` in `merkle-tools` performs no check that the leaf is a real transaction rather than an internal node: [4](#0-3) 

### Impact Explanation
A caller who supplies an internal Merkle tree node hash as `tx_id` — along with a valid sibling path — will cause `compute_root_from_merkle_proof` to reconstruct the correct `merkle_root`, and `verify_transaction_inclusion` returns `true`. Any downstream NEAR contract or application that relies on this result to authorise a cross-chain action (e.g., releasing funds, minting tokens, updating state) will be deceived into treating a non-existent transaction as confirmed.

### Likelihood Explanation
The attack requires no privileged role, no leaked key, and no social engineering. The deprecated function is callable by any NEAR account that can pay gas. The 64-byte Merkle forgery technique is publicly documented (referenced in the contract's own comments at line 278). The only prerequisite is knowledge of a valid block's Merkle tree structure, which is public Bitcoin data.

### Recommendation
Remove the `pub` visibility from `verify_transaction_inclusion` or gate it behind a role that no external caller holds. Alternatively, delete the function body and have it unconditionally panic with a message directing callers to `verify_transaction_inclusion_v2`. The `#[deprecated]` attribute in Rust is a compile-time lint only; it does not prevent on-chain invocation.

### Proof of Concept
1. Identify any mainchain block `B` stored in the contract with at least two transactions (`tx0`, `tx1`).
2. Compute the intermediate Merkle node `N = Hash(tx0 || tx1)` — this is a 32-byte value that is a valid internal node of the Merkle tree.
3. Construct a `ProofArgs` where:
   - `tx_id = N`
   - `tx_block_blockhash = B.hash`
   - `tx_index = 0` (position of `N` in the next level of the tree)
   - `merkle_proof` = the sibling path from `N` up to the root (one element shorter than a leaf proof)
   - `confirmations = 1`
4. Call `verify_transaction_inclusion(args)` directly on the contract.
5. The function computes `compute_root_from_merkle_proof(N, 0, proof)` which reconstructs `B.merkle_root` correctly, and returns `true` — falsely asserting that `N` (a non-existent transaction) is confirmed in block `B`. [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L263-286)
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
```

**File:** contract/src/lib.rs (L287-288)
```rust
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L315-323)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L358-368)
```rust
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

**File:** merkle-tools/src/lib.rs (L34-52)
```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    let mut current_position = transaction_position;

    for proof_hash in merkle_proof {
        if current_position % 2 == 0 {
            current_hash = compute_hash(&current_hash, proof_hash);
        } else {
            current_hash = compute_hash(proof_hash, &current_hash);
        }
        current_position /= 2;
    }

    current_hash
}
```
