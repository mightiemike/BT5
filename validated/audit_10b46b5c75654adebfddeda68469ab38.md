### Title
Phantom Transaction Inclusion: Odd-Tree Duplicate-Leaf Proof Accepted at Index N — (`merkle-tools/src/lib.rs`)

---

### Summary

`compute_root_from_merkle_proof` accepts a valid proof for the last leaf of an odd-length Merkle tree when submitted with a phantom `tx_index = N` (one past the last real transaction), because the duplicate-leaf sibling inserted by `merkle_proof_calculator` is identical to the leaf itself, making the hash commute regardless of which side it is placed on.

---

### Finding Description

`merkle_proof_calculator` handles odd-length trees by duplicating the last leaf before pairing: [1](#0-0) 

For a 3-tx tree `[tx0, tx1, tx2]`, the proof for index 2 is `[tx2, hash(tx0,tx1)]` — the first proof element is `tx2` itself (the duplicate sibling).

`compute_root_from_merkle_proof` uses `current_position % 2` to decide argument order: [2](#0-1) 

**Legitimate call** — `compute_root_from_merkle_proof(tx2, 2, [tx2, hash(tx0,tx1)])`:
- position=2 (even) → `hash(tx2, proof[0])` = `hash(tx2, tx2)` ✓

**Phantom call** — `compute_root_from_merkle_proof(tx2, 3, [tx2, hash(tx0,tx1)])`:
- position=3 (odd) → `hash(proof[0], tx2)` = `hash(tx2, tx2)` ✓

Because `proof[0] == tx2 == current_hash`, the two arguments to `compute_hash` are identical, so swapping them produces the same result. Both calls return the same root. Index 3 does not exist in the block.

Neither `verify_transaction_inclusion` nor `verify_transaction_inclusion_v2` bounds-checks `tx_index` against the actual number of transactions: [3](#0-2) 

The only guard is `require!(!args.merkle_proof.is_empty(), ...)`. [4](#0-3) 

`verify_transaction_inclusion_v2` adds a coinbase proof check, but it is independent — it verifies the coinbase at index 0 and does not constrain the attacker's `tx_index`: [5](#0-4) 

---

### Impact Explanation

Any downstream contract or bridge that calls `verify_transaction_inclusion` (or `_v2`) to authorize an action (e.g., mint wrapped tokens, release funds) can be triggered by a phantom transaction that never existed on-chain. The attacker needs only:
1. A real block with an odd number of transactions (extremely common — the majority of Bitcoin blocks have an odd tx count).
2. The public Merkle proof for the last real transaction (freely available from any block explorer or Bitcoin node).
3. Submit the same proof with `tx_index = N` instead of `N-1`.

---

### Likelihood Explanation

- No privileged role required; any NEAR account can call `verify_transaction_inclusion` or `verify_transaction_inclusion_v2`.
- Odd-length transaction counts are the norm in Bitcoin blocks.
- All required proof data is public.
- The attack is deterministic and requires no brute force.

---

### Recommendation

In `compute_root_from_merkle_proof`, reject any proof where a proof element equals the current hash (i.e., `proof_hash == current_hash`), as this is the fingerprint of a duplicate-leaf sibling and is never valid for a distinct transaction. Alternatively, enforce that `tx_index < 2^(proof.len())` AND that the proof length matches `ceil(log2(tx_count))` by passing the actual transaction count into the verifier and rejecting out-of-range indices.

---

### Proof of Concept

```rust
// 3-tx tree: [tx0, tx1, tx2]
let tx0 = H256::from([1u8; 32]);
let tx1 = H256::from([2u8; 32]);
let tx2 = H256::from([3u8; 32]);

let proof_for_index_2 = merkle_proof_calculator(vec![tx0, tx1, tx2.clone()], 2);
// proof_for_index_2[0] == tx2  (the duplicate sibling)

let root_real    = compute_root_from_merkle_proof(tx2.clone(), 2, &proof_for_index_2);
let root_phantom = compute_root_from_merkle_proof(tx2.clone(), 3, &proof_for_index_2);

assert_eq!(root_real, root_phantom); // passes — phantom index 3 accepted
```

Calling `verify_transaction_inclusion` on-chain with `tx_id=tx2`, `tx_index=3`, and `proof_for_index_2` against a block whose Merkle root was built from exactly 3 transactions returns `true`, falsely certifying inclusion of a non-existent transaction. [6](#0-5) [7](#0-6)

### Citations

**File:** merkle-tools/src/lib.rs (L4-31)
```rust
pub fn merkle_proof_calculator(tx_hashes: Vec<H256>, transaction_position: usize) -> Vec<H256> {
    let mut transaction_position = transaction_position;
    let mut merkle_proof = Vec::new();
    let mut current_hashes = tx_hashes;

    while current_hashes.len() > 1 {
        if current_hashes.len() % 2 == 1 {
            current_hashes.push(current_hashes[current_hashes.len() - 1].clone());
        }

        if transaction_position % 2 == 1 {
            merkle_proof.push(current_hashes[transaction_position - 1].clone());
        } else {
            merkle_proof.push(current_hashes[transaction_position + 1].clone());
        }

        let mut new_hashes = Vec::new();

        for i in (0..current_hashes.len() - 1).step_by(2) {
            new_hashes.push(compute_hash(&current_hashes[i], &current_hashes[i + 1]));
        }

        current_hashes = new_hashes;
        transaction_position /= 2;
    }

    merkle_proof
}
```

**File:** merkle-tools/src/lib.rs (L33-52)
```rust
#[must_use]
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
