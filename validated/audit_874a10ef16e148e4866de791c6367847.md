### Title
Duplicate-last-leaf position aliasing in `compute_root_from_merkle_proof` allows the same proof to verify two distinct `tx_index` values — (`merkle-tools/src/lib.rs`)

---

### Summary

`compute_root_from_merkle_proof` performs no bounds check on `transaction_position`. In a Bitcoin Merkle tree with an odd number of transactions, the last leaf is duplicated. This creates a structural ambiguity: the same `(tx_id, merkle_proof)` tuple verifies correctly for **both** `tx_index = N-1` (the real last transaction) and `tx_index = N` (the phantom duplicate position). `verify_transaction_inclusion` is a public, unpermissioned call that passes the attacker-controlled `tx_index` directly into this function, so the contract returns `true` for a position that does not correspond to any real transaction.

---

### Finding Description

**Root cause — `merkle-tools/src/lib.rs`, lines 34–52:**

`compute_root_from_merkle_proof` iterates over the proof elements, choosing left/right ordering based solely on `current_position % 2`, then halves the position. It never checks whether `transaction_position` is within the actual leaf count of the tree. [1](#0-0) 

**The arithmetic collision for an odd-width tree:**

Take a block with 3 real transactions `[T0, T1, T2]`. Bitcoin duplicates the last leaf, so the padded tree is `[T0, T1, T2, T2]`.

```
Level 0 (leaves): T0   T1   T2   T2*
Level 1:          H(T0,T1)  H(T2,T2)
Root:             H(H(T0,T1), H(T2,T2))
```

The canonical proof for `T2` at position **2** is `proof = [T2, H(T0,T1)]`.

Tracing `compute_root_from_merkle_proof(T2, 2, [T2, H(T0,T1)])`:
- pos=2 (even) → `H(T2, T2)` ; pos becomes 1
- pos=1 (odd)  → `H(H(T0,T1), H(T2,T2))` = root ✓

Tracing the **same proof** with `tx_index = 3` (phantom position):
- pos=3 (odd)  → `H(T2, T2)` ; pos becomes 1
- pos=1 (odd)  → `H(H(T0,T1), H(T2,T2))` = root ✓

Both calls return the correct Merkle root. `verify_transaction_inclusion` therefore returns `true` for `tx_index=3` even though no transaction occupies that slot. [2](#0-1) 

**Entrypoint — `contract/src/lib.rs`, lines 288–323:**

`verify_transaction_inclusion` is public (no `#[private]`, no `#[trusted_relayer]`). The attacker supplies all of `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, and `confirmations`. The function passes `tx_index` verbatim to `compute_root_from_merkle_proof` with no upper-bound validation. [3](#0-2) 

**`verify_transaction_inclusion_v2` does not close the gap:**

The v2 function adds a coinbase proof check (position 0), but then calls the deprecated v1 function internally. The coinbase check only validates proof depth; it does not constrain the `tx_index` of the target transaction. The same position-aliasing attack applies through v2. [4](#0-3) 

---

### Impact Explanation

A downstream bridge, unlock, mint, or withdrawal contract that uses `(block_hash, tx_index)` as its replay-protection key will accept two separate "inclusion proofs" for the same on-chain transaction — one at the real index and one at the phantom duplicate index. This enables the same Bitcoin payment to be credited twice on the NEAR side, constituting a cross-chain double-spend.

---

### Likelihood Explanation

- The entrypoint is fully public and requires no special role.
- The attacker needs only a real Bitcoin block with an odd transaction count (the majority of Bitcoin blocks historically have an odd number of transactions).
- The proof material (`tx_id`, sibling hash, parent hash) is entirely public on-chain Bitcoin data.
- No privileged key, relayer compromise, or social engineering is required.

---

### Recommendation

1. **Bound-check `transaction_position`** inside `compute_root_from_merkle_proof`: the caller must supply the total leaf count, and the function must reject any `transaction_position >= leaf_count`.
2. Alternatively, enforce the invariant at the `verify_transaction_inclusion` layer: require that `tx_index < 2^(proof.len()-1)` is not sufficient alone — the actual transaction count from the block header (or a committed leaf-count field) must be checked.
3. The duplicate-last-leaf ambiguity is a known Bitcoin protocol property; the fix is to treat the padded tree size as the authoritative bound and reject any index that falls in the duplicated phantom range.

---

### Proof of Concept

```rust
// Odd-width tree: 3 transactions [T0, T1, T2]
// Merkle root = H(H(T0,T1), H(T2,T2))
// Canonical proof for T2 at index 2: [T2, H(T0,T1)]

let proof = vec![T2.clone(), h_t0_t1.clone()];

// Legitimate call — returns true (expected)
let r1 = compute_root_from_merkle_proof(T2.clone(), 2, &proof);
assert_eq!(r1, merkle_root);

// Attack call — same tx_id, same proof, phantom index 3 — also returns true
let r2 = compute_root_from_merkle_proof(T2.clone(), 3, &proof);
assert_eq!(r2, merkle_root); // passes — position 3 does not exist in the real block

// Both verify_transaction_inclusion calls return true:
// call 1: tx_index=2  → bridge credits the deposit
// call 2: tx_index=3  → bridge credits the same deposit again (double-spend)
```

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
