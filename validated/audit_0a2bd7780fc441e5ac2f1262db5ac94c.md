After reading all production files, I have identified a concrete analog.

---

### Title
Deprecated `verify_transaction_inclusion` Still Publicly Callable, Enabling Merkle Proof Forgery via Internal-Node `tx_id` — (`File: contract/src/lib.rs`)

### Summary
The deprecated `verify_transaction_inclusion` method remains a live, unprivileged-callable NEAR contract entry point. It accepts a caller-supplied `tx_id` and verifies only that the hash sits at the claimed position in the Merkle tree — it never validates that the hash represents an actual transaction rather than an internal Merkle tree node. An attacker can supply an internal-node hash as `tx_id`, pair it with a valid sibling proof, and receive `true`, falsely proving Bitcoin transaction inclusion.

### Finding Description
`verify_transaction_inclusion` is annotated `#[deprecated]` in Rust source, but Rust's `deprecated` attribute is a **compiler lint only** — it does not remove the method from the compiled WASM binary or prevent on-chain invocation. Any NEAR account can call it directly via RPC.

The function's own documentation acknowledges the broken invariant:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash. We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification." [1](#0-0) 

The verification logic in `compute_root_from_merkle_proof` is purely positional: it hashes the supplied `tx_id` with each sibling in the proof path and checks whether the result equals the stored `merkle_root`. It has no mechanism to distinguish a leaf (real transaction hash) from an internal node. [2](#0-1) 

The corrected entry point `verify_transaction_inclusion_v2` mitigates this by requiring a coinbase proof of equal depth, which anchors the tree depth and prevents internal-node substitution. [3](#0-2) 

However, because `verify_transaction_inclusion` is never gated, removed, or access-controlled, callers can bypass `v2` entirely and invoke `v1` directly. [4](#0-3) 

### Impact Explanation
Any downstream NEAR contract or off-chain service that calls `verify_transaction_inclusion` to gate a real-world action (e.g., releasing bridged funds, confirming a cross-chain swap, crediting a deposit) can be deceived into accepting a fabricated proof. The attacker does not need to forge a Bitcoin block or break any cryptographic primitive — they only need to identify an internal Merkle node hash from a real block and construct the corresponding sibling path, both of which are derivable from public Bitcoin blockchain data.

The corrupted state is the **proof result** returned to the caller: `true` is returned for a transaction that was never broadcast or confirmed on Bitcoin.

### Likelihood Explanation
The attack requires no privileged role, no leaked key, and no social engineering. The attacker needs only:
1. A real Bitcoin block already accepted by the light client (public data).
2. The Merkle tree of that block (derivable from the block's transaction list, also public).
3. Any internal node hash from that tree and its sibling proof path.

The 64-byte transaction Merkle forgery technique is well-documented (referenced in the contract's own comments at line 268) and has known tooling. The entry point is open to any NEAR account. [5](#0-4) 

### Recommendation
Remove `verify_transaction_inclusion` from the compiled contract entirely, or gate it with an access-control role that prevents unprivileged callers from invoking it. The `#[deprecated]` attribute alone provides no on-chain protection. All callers must be migrated to `verify_transaction_inclusion_v2`.

### Proof of Concept
1. Identify a Bitcoin block already stored in the light client (e.g., via `get_block_hash_by_height`).
2. Obtain the full transaction list for that block from a public Bitcoin node.
3. Compute the Merkle tree. Select any internal node `N` at depth `d` and collect its sibling proof path `P` (length `d`).
4. Call `verify_transaction_inclusion` with:
   - `tx_id = N` (the internal node hash)
   - `tx_block_blockhash` = the real block hash
   - `tx_index` = the position of `N` within its level of the tree
   - `merkle_proof = P`
   - `confirmations = 1`
5. `compute_root_from_merkle_proof(N, position, P)` will reconstruct the real `merkle_root`, and the function returns `true` — falsely asserting that the non-existent "transaction" `N` is confirmed in that block. [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L263-270)
```rust
    /// Verifies that a transaction is included in a block at a given block height
    ///
    /// # Deprecated
    /// Use [`verify_transaction_inclusion_v2`] instead, which includes coinbase merkle proof validation
    /// to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
    /// @param `tx_id` transaction identifier
```

**File:** contract/src/lib.rs (L276-323)
```rust
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
