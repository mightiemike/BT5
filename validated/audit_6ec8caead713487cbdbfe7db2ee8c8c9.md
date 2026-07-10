After reading all production source files, I have identified a concrete analog.

---

### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable Without Coinbase-Proof Anchor, Enabling 64-Byte Merkle Forgery — (`contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` is still an unrestricted public entry point on the NEAR contract. It verifies SPV proofs by computing a Merkle root from a caller-supplied `tx_id` and `merkle_proof`, but it performs no check that `tx_id` is a real leaf-level transaction hash rather than an internal Merkle tree node. An attacker can supply an internal node of any real Bitcoin block's Merkle tree as `tx_id`, construct the matching proof path, and cause the function to return `true` for a transaction that was never included in that block. Any downstream NEAR contract that gates a privileged action on this return value is vulnerable to proof forgery.

### Finding Description
`verify_transaction_inclusion` (lines 288–323, `contract/src/lib.rs`) reduces the entire verification to:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [1](#0-0) 

It performs no check that `args.tx_id` is a leaf-level transaction hash rather than an internal Merkle tree node. The 64-byte transaction Merkle proof forgery attack (https://www.bitmex.com/blog/64-Byte-Transactions) exploits exactly this gap: an internal node at depth 1 of a real block's Merkle tree is `SHA256d(tx₀ ‖ tx₁)` — 32 bytes, indistinguishable in format from a real `txid`. An attacker can present this internal node as `tx_id`, supply the sibling subtree as `merkle_proof`, and the root computation will match the real block's `merkle_root`, causing the function to return `true`.

The contract's own docstring acknowledges this:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [2](#0-1) 

`verify_transaction_inclusion_v2` (lines 347–369) was introduced to close this gap by requiring a coinbase Merkle proof that anchors the proof tree to a known-valid leaf (position 0, the coinbase transaction):

```rust
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
``` [3](#0-2) 

However, the deprecated `verify_transaction_inclusion` remains a live, unrestricted public entry point. The Rust `#[deprecated]` attribute is a compile-time hint only; it imposes no runtime restriction. The function is `pub` and carries only `#[pause]`, meaning it is callable by any NEAR account unless the PauseManager explicitly pauses it. [4](#0-3) 

### Impact Explanation
The corrupted value is the **proof result**: `verify_transaction_inclusion` returns `true` for a forged `tx_id`. The broken invariant is: the function must return `true` only when `tx_id` is a real transaction hash included in the specified block at the claimed index. This invariant is violated for any internal Merkle tree node of that block. Any NEAR contract that calls this function to gate a privileged action — releasing bridged assets, minting tokens, recording a cross-chain event — can be deceived into executing that action without a corresponding real Bitcoin transaction.

### Likelihood Explanation
Medium. All Bitcoin block Merkle trees are public. An attacker can inspect any confirmed block already accepted by the contract, extract an internal node at depth 1, compute the corresponding proof path, and submit it to `verify_transaction_inclusion`. No mining, key material, or privileged access is required. The only prerequisite is that a downstream consumer calls the deprecated function rather than `verify_transaction_inclusion_v2`.

### Recommendation
- Add a runtime guard inside `verify_transaction_inclusion` (e.g., `env::panic_str("use verify_transaction_inclusion_v2")`) to prevent any external call from reaching the vulnerable path.
- Alternatively, mark the function `#[private]` so only the contract itself can call it (as `verify_transaction_inclusion_v2` already does internally via `#[allow(deprecated)]`).
- Migrate all known downstream callers to `verify_transaction_inclusion_v2`.

### Proof of Concept
1. Select any confirmed Bitcoin block B already accepted by the contract, with ≥ 2 transactions (tx₀, tx₁, …).
2. Compute the depth-1 internal node: `node = SHA256d(tx₀ ‖ tx₁)` (32 bytes, same format as a `txid`).
3. Build the Merkle proof path from `node` up to `merkle_root(B)` using the remaining sibling hashes at each level.
4. Call `verify_transaction_inclusion` with:
   - `tx_id = node`
   - `tx_block_blockhash = hash(B)`
   - `tx_index = 0` (position of `node` in the level above the leaves)
   - `merkle_proof = [sibling hashes from node to root]`
   - `confirmations = 1`
5. `compute_root_from_merkle_proof(node, 0, proof)` reproduces `merkle_root(B)`, so the function returns `true` — even though `node` is not a real transaction. [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

**File:** contract/src/lib.rs (L283-289)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
```

**File:** contract/src/lib.rs (L317-323)
```rust
        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L358-365)
```rust
        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );
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
