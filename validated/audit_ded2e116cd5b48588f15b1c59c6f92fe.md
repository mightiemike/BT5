### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable On-Chain, Enabling 64-Byte Merkle Proof Forgery — (`contract/src/lib.rs`)

---

### Summary

The `verify_transaction_inclusion` function is marked `#[deprecated]` but remains a fully accessible public NEAR method. Any unprivileged caller can invoke it directly, bypassing the coinbase-proof protection introduced in `verify_transaction_inclusion_v2`. This breaks the contract's own stated security invariant that transaction inclusion verification must be forgery-resistant.

---

### Finding Description

The contract exposes two transaction-verification entry points:

- `verify_transaction_inclusion` (v1): deprecated, no coinbase-proof check
- `verify_transaction_inclusion_v2`: secure replacement, adds coinbase-proof validation to defeat the 64-byte Merkle forgery attack [1](#0-0) 

Rust's `#[deprecated]` attribute is a **compile-time lint only**. It emits a warning to callers that compile against the crate, but it imposes zero runtime restriction. Because the function is declared `pub fn` and carries no access-control guard beyond `#[pause]` (which is inactive by default), any NEAR account can call `verify_transaction_inclusion` directly as an on-chain transaction, completely bypassing `verify_transaction_inclusion_v2`. [2](#0-1) 

The v1 function passes the caller-supplied `tx_id` directly into `compute_root_from_merkle_proof` without any check that `tx_id` is a leaf node (an actual transaction) rather than an internal Merkle-tree node. [3](#0-2) 

`compute_root_from_merkle_proof` is purely positional: it hashes whatever `transaction_hash` it receives with the supplied siblings and returns the resulting root. It cannot distinguish a real transaction hash from an internal node hash.

The 64-byte forgery works as follows: Bitcoin's Merkle tree computes `SHA256d(left_child || right_child)` at every internal node. An attacker who knows the full transaction list of a block can compute any internal node hash `N = SHA256d(A || B)` and then construct a valid sibling path from `N` up to the Merkle root. Supplying `N` as `tx_id` to v1 causes the function to return `true` even though `N` is not a transaction.

The v2 function prevents this by first verifying a coinbase proof (the coinbase is always at index 0 and is a genuine leaf), which constrains the tree structure and makes internal-node forgery computationally infeasible. [4](#0-3) 

Because v1 is still reachable, the entire v2 protection can be bypassed by calling v1 directly.

---

### Impact Explanation

A malicious proof submitter calls `verify_transaction_inclusion` with a crafted internal Merkle node hash as `tx_id` and a valid sibling path. The function returns `true` for a Bitcoin transaction that does not exist. Any consumer contract (bridge, atomic-swap protocol, cross-chain lending) that gates an action on this return value will be deceived into releasing funds or executing logic for a non-existent Bitcoin transaction. This breaks the core security invariant of the light client: that SPV verification is forgery-resistant.

---

### Likelihood Explanation

The attack requires no special privileges. Any NEAR account can call `verify_transaction_inclusion` directly. The full transaction list of every Bitcoin block is publicly available, so computing internal node hashes and constructing sibling paths is straightforward. The only prerequisite is that the target block is already in the light client's main chain, which is the normal operating state.

---

### Recommendation

Remove `verify_transaction_inclusion` from the public API entirely, or restrict it to internal use only (make it `fn` instead of `pub fn`). The `#[deprecated]` attribute provides no on-chain protection. If backward compatibility is required, add the same coinbase-proof validation that `verify_transaction_inclusion_v2` performs before delegating to the shared logic.

---

### Proof of Concept

1. Identify any Bitcoin block already accepted into the light client's main chain (e.g., block at height `H`).
2. Obtain the full transaction list for that block from a Bitcoin node.
3. Compute an internal Merkle node `N = SHA256d(tx[0] || tx[1])` (the parent of the first two transactions).
4. Construct the sibling path from `N` up to the Merkle root (one level shorter than a leaf proof).
5. Call `verify_transaction_inclusion` with:
   - `tx_id = N` (the internal node hash, not a real transaction)
   - `tx_block_blockhash` = the block hash at height `H`
   - `tx_index` = the position corresponding to `N` in the next-level tree (e.g., `0`)
   - `merkle_proof` = the sibling path from `N` to the root
   - `confirmations` = any value ≤ `gc_threshold`
6. The function returns `true` despite no such transaction existing on Bitcoin. [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L283-288)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
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
