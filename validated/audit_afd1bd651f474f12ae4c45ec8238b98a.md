### Title
Odd-Leaf Duplicate Position Ambiguity Allows Same Transaction Proven at Two Distinct `tx_index` Values — (`merkle-tools/src/lib.rs`, `contract/src/lib.rs`)

---

### Summary

`compute_root_from_merkle_proof` performs no bounds check on `transaction_position`. In a Bitcoin Merkle tree with an odd number of transactions, the last leaf is duplicated to pad the level to even width. This means the identical proof bytes and the identical `tx_id` produce a valid root for **both** the real position `N-1` and the phantom position `N`. `verify_transaction_inclusion` passes the caller-supplied `tx_index` directly into this function without any validation, so it returns `true` for both positions. Any downstream contract that gates value release on `(tx_id, tx_index)` — rather than on `tx_id` alone — can be drained twice for the same on-chain Bitcoin event.

---

### Finding Description

**`compute_root_from_merkle_proof` — no position bounds check** [1](#0-0) 

The function accepts any `transaction_position` value. It uses only `current_position % 2` (left/right) and `current_position /= 2` (level step). There is no check that the position is less than the actual leaf count.

**Odd-leaf duplication creates two valid positions for the same hash**

Consider a 5-transaction block `[T0, T1, T2, T3, T4]`. Bitcoin's Merkle construction pads odd-width levels by duplicating the last element:

```
Level 0 (padded): [T0, T1, T2, T3, T4, T4]
Level 1 (padded): [H(T0,T1), H(T2,T3), H(T4,T4), H(T4,T4)]
Level 2:          [H(H(T0,T1),H(T2,T3)), H(H(T4,T4),H(T4,T4))]
Root:             H(level2[0], level2[1])
```

The canonical proof for `T4` at position 4 is `[T4, H(T4,T4), H(H(T0,T1),H(T2,T3))]`.

Tracing `compute_root_from_merkle_proof` for **position 4**:
- step 1: pos=4 (even) → `H(T4, T4)`, pos→2
- step 2: pos=2 (even) → `H(H(T4,T4), H(T4,T4))`, pos→1
- step 3: pos=1 (odd) → `H(H(H(T0,T1),H(T2,T3)), H(H(T4,T4),H(T4,T4)))` = Root ✓

Tracing the **same proof bytes** for **position 5**:
- step 1: pos=5 (odd) → `H(T4, T4)`, pos→2
- step 2: pos=2 (even) → `H(H(T4,T4), H(T4,T4))`, pos→1
- step 3: pos=1 (odd) → same Root ✓

Both positions yield the same root. `verify_transaction_inclusion` passes the caller-supplied `tx_index` directly: [2](#0-1) 

No guard exists between the caller-controlled `args.tx_index` and the proof computation. The function returns `true` for both `tx_index=4` and `tx_index=5` with the same `tx_id` and the same `merkle_proof`.

**`verify_transaction_inclusion_v2` is equally affected**

The v2 function adds a coinbase proof check but then delegates to the deprecated v1 function: [3](#0-2) 

The coinbase check is at fixed position 0 and does not constrain the `tx_index` of the target transaction. The duplicate-position ambiguity is fully inherited.

---

### Impact Explanation

Any NEAR contract that:
1. calls `verify_transaction_inclusion` (or v2) to authorize a value transfer, and
2. deduplicates by `(tx_id, tx_index)` or `(tx_id, tx_index, blockhash)` rather than by `tx_id` alone

can be exploited for double redemption of the same Bitcoin event:

- **Call 1**: `tx_id=T4, tx_index=4, blockhash=B` → returns `true` → downstream records `(T4, 4, B)` as spent
- **Call 2**: `tx_id=T4, tx_index=5, blockhash=B` → returns `true` → downstream records `(T4, 5, B)` as spent → second payout

No reorg is required for the basic double-spend. A reorg can additionally be used to reset any block-height-based or confirmation-window-based deduplication in the calling contract, enabling a third or fourth claim after the block temporarily leaves and re-enters the canonical chain.

---

### Likelihood Explanation

- The entrypoint is fully public (`#[pause]`, no `#[private]`, no `#[trusted_relayer]`).
- The attacker controls all five proof arguments: `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`.
- Any Bitcoin block with an odd transaction count (the majority of mainnet blocks) provides a suitable target.
- The exploit requires no privileged role, no leaked key, and no social engineering.
- The only prerequisite is that a downstream contract uses `(tx_id, tx_index)` as its deduplication key, which is a natural design choice when the light client advertises `tx_index` as a meaningful proof parameter.

---

### Recommendation

1. **In `compute_root_from_merkle_proof`**: Accept an explicit `leaf_count: usize` parameter and reject any `transaction_position >= leaf_count` before entering the loop.
2. **In `verify_transaction_inclusion` / v2**: Require callers to supply the block's transaction count and enforce `tx_index < tx_count` before calling the Merkle verifier.
3. **Documentation**: Explicitly warn that `tx_index` must be validated against the actual transaction count of the block, and that the phantom duplicate position in odd-width trees is not a valid transaction position.

---

### Proof of Concept

```rust
// 5-transaction block: T0..T4
// Canonical proof for T4 at position 4:
let proof = vec![T4.clone(), H_T4_T4.clone(), H_left_subtree.clone()];

// Both of these return true against the same block:
assert!(verify_transaction_inclusion(tx_id=T4, tx_index=4, blockhash=B, proof=proof.clone()));
assert!(verify_transaction_inclusion(tx_id=T4, tx_index=5, blockhash=B, proof=proof.clone()));

// Downstream contract keyed on (tx_id, tx_index) pays out twice.
```

The arithmetic follows directly from the `current_position % 2` / `current_position /= 2` loop in `compute_root_from_merkle_proof`: [4](#0-3) 

Position 4 and position 5 diverge only at step 1 (even vs. odd), but both produce `H(T4, T4)` as the first intermediate — after which the paths are identical and both reach the correct root.

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

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```
