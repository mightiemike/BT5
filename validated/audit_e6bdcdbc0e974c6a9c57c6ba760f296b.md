### Title
Deprecated `verify_transaction_inclusion` Accepts Forged Merkle Proofs via Internal Node Hash Substitution — (`contract/src/lib.rs`)

---

### Summary

The public `verify_transaction_inclusion` function does not validate that the caller-supplied `tx_id` is a leaf-level transaction hash. Any unprivileged NEAR caller can substitute an internal Merkle tree node hash for `tx_id`, supply a correspondingly shorter proof path, and receive a `true` return value — a forged proof of transaction inclusion — without any real Bitcoin transaction existing at that position.

---

### Finding Description

`verify_transaction_inclusion` computes `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof)` and compares the result to `header.block_header.merkle_root`. [1](#0-0) 

The function imposes no constraint that `tx_id` must be a leaf-level transaction hash. An attacker can supply an internal Merkle tree node hash — for example, `H(T1, T2)` from a 4-transaction block — as `tx_id`, paired with a one-element proof `[H(T3, T4)]`. The computation then yields:

```
compute_root_from_merkle_proof(H(T1,T2), 0, [H(T3,T4)])
  = H(H(T1,T2), H(T3,T4))
  = merkle_root   ✓
```

The function returns `true`, falsely certifying inclusion of a non-existent transaction.

The function's own documentation acknowledges this gap: [2](#0-1) 

Despite being marked `#[deprecated]`, the function remains a live, callable public NEAR contract method. Rust's `#[deprecated]` attribute emits only a compiler warning for callers; it imposes no runtime restriction. Any NEAR account — including a consumer contract that has not yet migrated — can invoke it directly. [3](#0-2) 

The `compute_root_from_merkle_proof` implementation in `merkle-tools` is a pure path-reconstruction function with no leaf-vs-internal-node distinction: [4](#0-3) 

The only guard present — `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` — does not prevent the attack; the attacker supplies a non-empty (but shorter) proof path. [5](#0-4) 

The `verify_transaction_inclusion_v2` function was introduced specifically to close this gap by requiring a coinbase proof of equal depth, but the v1 function was never removed. [6](#0-5) 

---

### Impact Explanation

Any consumer contract that calls `verify_transaction_inclusion` can be deceived into accepting a forged proof of Bitcoin transaction inclusion. An attacker can claim that a Bitcoin transaction (e.g., a cross-chain payment, atomic swap settlement, or bridged asset release) was confirmed in a block when no such transaction exists at the claimed position. This directly corrupts the proof result returned by the light client and can trigger fraudulent state transitions in downstream contracts that trust the `true` return value.

---

### Likelihood Explanation

**Medium

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

**File:** contract/src/lib.rs (L283-288)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L315-315)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
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
