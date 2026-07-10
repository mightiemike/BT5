### Title
Out-of-Range `tx_index` Bypasses Merkle Inclusion Check — (`merkle-tools/src/lib.rs`)

---

### Summary

`compute_root_from_merkle_proof` contains no bounds check between `transaction_position` and the proof length or actual tree size. An unprivileged NEAR caller can supply a `tx_index` that is a power-of-two multiple of the real leaf count (e.g., `tx_index = 4` for a 4-tx tree) together with the legitimate proof for `tx_index = 0`, and the function will compute the identical root hash — causing `verify_transaction_inclusion` to return `true` for a position that holds no real transaction.

---

### Finding Description

`compute_root_from_merkle_proof` iterates over the proof array, using only `current_position % 2` to choose left/right at each level, then halves the position: [1](#0-0) 

There is no assertion that `transaction_position < 2^(proof.len())` or that `transaction_position` is within the actual leaf count of the committed block. `verify_transaction_inclusion` passes the caller-supplied `args.tx_index` directly: [2](#0-1) 

**Why the all-even-position path is the key:** For any `tx_index` that is a multiple of `2^k` (where `k = proof.len()`), every iteration sees an even `current_position`, so the function always takes the left branch. Concretely, for a 4-tx tree (depth 2, proof length 2):

| `tx_index` | Level 0 pos | Branch | Level 1 pos | Branch | Result |
|---|---|---|---|---|---|
| 0 | 0 (even) | left | 0 (even) | left | `hash(hash(T0,T1), H23)` |
| 4 | 4 (even) | left | 2 (even) | left | `hash(hash(T0,T1), H23)` |

Both produce the identical computation. The proof `[T1, H23]` is valid for both `tx_index=0` and `tx_index=4`, yet index 4 does not exist in a 4-tx tree.

`verify_transaction_inclusion_v2` does not fix this; it adds a coinbase proof check with a hardcoded `0usize` position, then delegates to the same vulnerable `verify_transaction_inclusion`: [3](#0-2) 

---

### Impact Explanation

An attacker can call `verify_transaction_inclusion` (a public, unpermissioned NEAR view/call) with:
- `tx_id = T0` (a real transaction hash from the block)
- `tx_index = 4` (out of range for a 4-tx block)
- `merkle_proof = [T1, H23]` (the legitimate proof for index 0)

The function returns `true`, asserting that `T0` is at position 4 — a position that holds no real transaction. Any downstream system that trusts the `(tx_id, tx_index)` pair as a unique, positionally-correct inclusion claim is deceived. This directly satisfies the scoped critical impact: **"wrong index" inclusion claim accepted as valid**.

---

### Likelihood Explanation

The entrypoint is fully public — no privileged role, no relayer key, no `#[private]` guard. The attacker only needs to know one real transaction hash and its legitimate proof from the block, both of which are publicly available on-chain. The crafted call requires no cryptographic forgery; it reuses existing proof elements with a shifted index.

---

### Recommendation

Add a bounds check in `compute_root_from_merkle_proof` (or at the call site in `verify_transaction_inclusion`) asserting that `transaction_position < (1 << merkle_proof.len())`. This ensures the supplied index is representable within a tree of the claimed depth:

```rust
// In compute_root_from_merkle_proof or verify_transaction_inclusion:
assert!(
    transaction_position < (1usize << merkle_proof.len()),
    "tx_index out of range for proof of this length"
);
```

Additionally, if the contract has access to the actual transaction count for the block, validate `tx_index < tx_count` directly.

---

### Proof of Concept

```rust
#[test]
fn test_out_of_range_tx_index_accepted() {
    // 4-tx tree: T0, T1, T2, T3
    let t0 = compute_hash_leaf(b"tx0");
    let t1 = compute_hash_leaf(b"tx1");
    let t2 = compute_hash_leaf(b"tx2");
    let t3 = compute_hash_leaf(b"tx3");

    let h01 = compute_hash(&t0, &t1);
    let h23 = compute_hash(&t2, &t3);
    let real_root = compute_hash(&h01, &h23);

    // Legitimate proof for T0 at index 0
    let proof = vec![t1.clone(), h23.clone()];

    // Valid: tx_index=0
    let root_0 = compute_root_from_merkle_proof(t0.clone(), 0, &proof);
    assert_eq!(root_0, real_root);

    // Exploit: tx_index=4 (out of range), same proof, same tx_id
    let root_4 = compute_root_from_merkle_proof(t0.clone(), 4, &proof);
    assert_eq!(root_4, real_root); // passes — wrong index accepted
}
```

Both assertions pass. `verify_transaction_inclusion` with `tx_index=4` returns `true` against the real block merkle root, despite index 4 not existing in the 4-tx tree. [4](#0-3)

### Citations

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

**File:** contract/src/lib.rs (L318-322)
```rust
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
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
