### Title
Proof Forgery via Unvalidated `tx_id` Leaf-Node Check in `verify_transaction_inclusion` Allows False Transaction Inclusion Acceptance — (`contract/src/lib.rs`)

---

### Summary

The `verify_transaction_inclusion` function accepts a fully attacker-controlled `tx_id` (H256) and passes it directly into `merkle_tools::compute_root_from_merkle_proof` without any validation that the supplied hash corresponds to an actual leaf node (transaction) rather than an internal Merkle tree node. An unprivileged NEAR caller can supply the hash of an internal Merkle node as `tx_id`, pair it with a valid Merkle proof for that node's position, and cause the function to return `true` — falsely certifying that a non-existent Bitcoin transaction was included in a confirmed block.

---

### Finding Description

`verify_transaction_inclusion` is a public, pausable NEAR contract method callable by any account:

```rust
pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
    // ...confirmations and chain membership checks...
    require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

    merkle_tools::compute_root_from_merkle_proof(
        args.tx_id,
        usize::try_from(args.tx_index).unwrap(),
        &args.merkle_proof,
    ) == header.block_header.merkle_root
}
``` [1](#0-0) 

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` treats the supplied `transaction_hash` as an opaque 32-byte value and hashes it upward through the tree without any check that it is a leaf:

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
``` [2](#0-1) 

Because the function accepts any 32-byte value as `tx_id`, an attacker who knows the transaction list of a confirmed Bitcoin block can:

1. Compute the hash of any internal Merkle node `N` at tree depth `d` and position `P`.
2. Construct a valid Merkle proof from `N` up to the Merkle root (using the sibling hashes at each level above `d`).
3. Call `verify_transaction_inclusion` with `tx_id = N`, `tx_index = P`, and the crafted proof.
4. `compute_root_from_merkle_proof` will correctly reproduce the stored Merkle root, so the function returns `true`.

The contract's own documentation acknowledges this:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [3](#0-2) 

The function is marked `#[deprecated]` but remains fully callable on-chain — Rust's `#[deprecated]` attribute only emits a compiler warning; it does not remove the public NEAR method. Any NEAR account can invoke it directly, bypassing `verify_transaction_inclusion_v2`. [4](#0-3) 

---

### Impact Explanation

Downstream NEAR contracts that call `verify_transaction_inclusion` to gate cross-chain actions (e.g., releasing bridged assets, minting tokens, or recording settlement) will receive a `true` result for a Bitcoin transaction that does not exist. This enables an attacker to:

- Claim that an arbitrary Bitcoin payment was made and confirmed, without ever broadcasting a real transaction.
- Trigger any downstream contract logic that is conditioned on a verified Bitcoin inclusion proof.

The corrupted value is the **proof result** (`true` vs. `false`), which is the sole security guarantee this contract provides to its consumers.

---

### Likelihood Explanation

The attack requires no privileged role, no leaked key, and no social engineering. Any NEAR account can call `verify_transaction_inclusion` directly. The attacker only needs to know the transaction list of any confirmed Bitcoin block (publicly available from any Bitcoin node or block explorer) to compute the internal node hashes and construct a valid forgery proof. The function is live and reachable on every deployed chain variant (Bitcoin, Litecoin, Dogecoin, Zcash) because it is defined in the shared `lib.rs`.

---

### Recommendation

1. **Remove or gate the deprecated endpoint.** The `verify_transaction_inclusion` function should either be removed from the public ABI or restricted to a privileged role so that external callers cannot invoke it directly.
2. **Enforce leaf-node validation in `compute_root_from_merkle_proof`.** The Merkle proof verifier should require that the proof length equals `log2(tree_size)`, and callers should supply the total number of transactions in the block so the library can confirm the supplied position is a leaf.
3. **Require all callers to use `verify_transaction_inclusion_v2`.** The v2 function's coinbase proof requirement significantly constrains the attack surface by anchoring the tree structure, but it is only effective if v1 is not directly callable.

---

### Proof of Concept

1. Attacker identifies a confirmed Bitcoin block `B` with Merkle root `R` and `N` transactions `[T0, T1, T2, T3]`.
2. Attacker computes the internal node `I = SHA256d(SHA256d(T0 ‖ T1) ‖ SHA256d(T2 ‖ T3))` — this is the root of the left subtree, at position 0 of depth 1.
3. Attacker constructs a one-element Merkle proof: the sibling of `I` at depth 1, which is `SHA256d(T2 ‖ T3)`.
4. Attacker calls `verify_transaction_inclusion` with `tx_id = I`, `tx_index = 0`, `merkle_proof = [SHA256d(T2 ‖ T3)]`, `tx_block_blockhash = B`, `confirmations = 1`.
5. `compute_root_from_merkle_proof(I, 0, [sibling])` computes `SHA256d(I ‖ sibling) = R`.
6. The function returns `true`. No transaction with hash `I` exists on Bitcoin. [2](#0-1) [5](#0-4)

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
