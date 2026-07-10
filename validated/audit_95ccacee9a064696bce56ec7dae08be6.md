### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing Coinbase Merkle Proof Check — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` is still a live, unpermissioned public entry point on the contract. It permanently lacks the coinbase Merkle proof check that `verify_transaction_inclusion_v2` enforces. Any unprivileged NEAR caller can invoke it directly, bypassing the only defense against the 64-byte transaction Merkle proof forgery attack and obtaining a `true` proof result for a transaction that was never included in a Bitcoin block.

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to fix the 64-byte transaction Merkle proof forgery vulnerability (documented at https://www.bitmex.com/blog/64-Byte-Transactions). It does so by first verifying a coinbase Merkle proof against the block's `merkle_root`, then delegating to the original function: [1](#0-0) 

The coinbase proof check is the critical invariant: [2](#0-1) 

However, `verify_transaction_inclusion` is still decorated only with `#[pause]` — no role restriction — making it callable by any NEAR account: [3](#0-2) 

The function itself acknowledges the vulnerability in its own doc comment: [4](#0-3) 

The function's only check is a Merkle path computation against the stored `merkle_root`: [5](#0-4) 

Without the coinbase proof anchor, an attacker can supply a `tx_id` that is actually a 64-byte internal Merkle tree node. Because Bitcoin's Merkle tree uses the same double-SHA256 for both leaf and internal nodes, a valid Merkle path can be constructed from any internal node to the root, and the function returns `true`.

The `#[deprecated]` Rust attribute is a compiler hint only — it does not restrict on-chain callability. The function remains a fully reachable NEAR contract method.

### Impact Explanation

Any downstream NEAR contract or off-chain application that calls `verify_transaction_inclusion` to gate a cross-chain action (e.g., releasing bridged assets, confirming a payment) can be deceived into accepting a forged proof. The attacker obtains a `true` return value for a Bitcoin transaction that does not exist, corrupting the proof result that the light client is designed to guarantee.

### Likelihood Explanation

The attack requires no privileged role, no leaked key, and no social engineering. Any NEAR account can call `verify_transaction_inclusion` directly. The 64-byte transaction forgery technique is well-documented and has known tooling. The only precondition is knowledge of a Bitcoin block's Merkle tree structure, which is public data.

### Recommendation

Remove the `pub` visibility from `verify_transaction_inclusion` or restrict it to an internal-only call path so it cannot be invoked directly by external callers. Alternatively, add an explicit `require!(false, "use verify_transaction_inclusion_v2")` guard at the top of the function body to make the deprecation a hard on-chain enforcement rather than a compiler hint. The v2 function should be the sole externally callable proof verification entry point.

### Proof of Concept

1. Identify any Bitcoin block already accepted by the contract (its `tx_block_blockhash` is in `mainchain_header_to_height`).
2. Obtain the block's full Merkle tree. Pick any internal node `N` at depth `d`; its 32-byte hash is the candidate `tx_id`.
3. Construct a Merkle proof of length `d` from `N` up to the `merkle_root` using the sibling hashes at each level. This proof is valid because `compute_root_from_merkle_proof` applies the same hash function at every level with no leaf/internal distinction.
4. Call `verify_transaction_inclusion` with `tx_id = N`, `tx_index` matching the position of `N` in the tree, and the constructed `merkle_proof`.
5. The function returns `true`. No such transaction exists in the block.

`verify_transaction_inclusion_v2` would reject this at step 4 because the coinbase proof check anchors the tree at index 0 and prevents an internal node from being presented as a leaf transaction. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L276-279)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
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
