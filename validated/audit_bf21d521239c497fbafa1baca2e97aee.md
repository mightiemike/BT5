### Title
Duplicate-Last-Leaf Position Ambiguity Allows Phantom `tx_index` Verification — (`merkle-tools/src/lib.rs::compute_root_from_merkle_proof`)

---

### Summary

`compute_root_from_merkle_proof` never validates that the caller-supplied `transaction_position` is within the actual bounds of the transaction list. In an odd-width Bitcoin Merkle tree the last leaf is duplicated during tree construction. This makes the same proof bytes and the same `tx_id` produce the correct Merkle root for **two distinct `tx_index` values**: the real last position `N-1` and the phantom position `N` (which has no corresponding transaction in the block). `verify_transaction_inclusion_v2` inherits this flaw and returns `true` for the phantom index, bypassing the light-client oracle's core invariant.

---

### Finding Description

**Root cause — `compute_root_from_merkle_proof`** [1](#0-0) 

The function uses `transaction_position` only to decide left/right placement at each level (`current_position % 2`). It never checks that the position is within `[0, actual_tx_count)`. No upper-bound guard exists anywhere in the call chain.

**Why the duplicate-leaf makes two positions equivalent**

Bitcoin pads an odd-length transaction list by appending a copy of the last leaf before hashing each level. For a block with N=3 transactions `[T0, T1, T2]` the padded level is `[T0, T1, T2, T2]`.

The canonical proof for T2 at position 2 is `proof = [T2, H(T0,T1)]`.

Tracing `compute_root_from_merkle_proof` with `tx_id=T2`, `tx_index=2`:
```
step 1: pos=2 (even)  → H(T2,  T2)        = P;  pos → 1
step 2: pos=1 (odd)   → H(H(T0,T1), P)    = root ✓
```

Tracing the **same proof** with `tx_id=T2`, `tx_index=3` (phantom, does not exist):
```
step 1: pos=3 (odd)   → H(T2,  T2)        = P;  pos → 1
step 2: pos=1 (odd)   → H(H(T0,T1), P)    = root ✓
```

Both paths produce the same root. Position 3 does not correspond to any transaction in the block, yet the function returns the correct root.

**Why the coinbase guard in v2 does not close the gap** [2](#0-1) 

The coinbase check verifies a separate, independent proof at position 0. It constrains `coinbase_tx_id` but places no constraint on `tx_index` of the target transaction. An attacker supplies a genuine coinbase proof from the real block alongside the phantom-position tx proof; both checks pass independently.

**Full call path** [3](#0-2) 

`verify_transaction_inclusion_v2` → `verify_transaction_inclusion` → `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof)` — no bounds check at any layer.

---

### Impact Explanation

`verify_transaction_inclusion_v2` is the authoritative oracle that downstream bridges, unlock contracts, mint contracts, and withdrawal flows rely on. When it returns `true` for phantom position N (a position that holds no transaction in the block), it asserts the existence of a transaction that does not exist at that index.

Concrete replay path:
1. A real transaction T_{N-1} is included in a block with an odd transaction count N.
2. A legitimate user submits a bridge withdrawal using `tx_index = N-1`; the bridge records `(tx_id, blockhash, N-1)` as spent.
3. The attacker re-submits the same `tx_id` with `tx_index = N` and the identical proof bytes.
4. `verify_transaction_inclusion_v2` returns `true`.
5. If the bridge keys its replay map on `(tx_id, blockhash, tx_index)`, the entry `(tx_id, blockhash, N)` is absent and the withdrawal is processed a second time — double-spend / double-mint.

Even if the bridge keys only on `(tx_id, blockhash)`, the oracle still returns `true` for a position that does not exist, which is a broken invariant that any future consumer of the API can be tricked by.

---

### Likelihood Explanation

- Every Bitcoin block with an odd transaction count (roughly half of all blocks) is a valid target.
- The attacker needs only a canonical block already accepted by the light client, a valid coinbase proof for that block (publicly derivable from the block), and the last transaction's proof (also public).
- No privileged role, no relayer compromise, no social engineering, and no special timing is required. The call is fully permissionless.

---

### Recommendation

1. **Enforce position bounds in `compute_root_from_merkle_proof`**: require that `transaction_position < (1 << merkle_proof.len())` is a necessary but not sufficient check; the real fix is to pass the actual transaction count and reject any `tx_index >= tx_count`.

2. **Encode `tx_count` in the proof arguments** (`ProofArgsV2`) and verify `tx_index < tx_count` before calling `compute_root_from_merkle_proof`. The `tx_count` itself can be committed to via the coinbase witness or an auxiliary proof.

3. **Alternatively**, detect the duplicate-leaf case: if `tx_index` is even and `proof[0] == tx_id`, the position is ambiguous (it could be the real last leaf or the phantom duplicate); reject it unless `tx_index` is provably the canonical last position.

---

### Proof of Concept

Construct a 3-transaction block (odd N=3). The canonical proof for the last transaction T2 at index 2 is `[T2, H(T0,T1)]`.

```rust
// Both calls below return `true` against the same block header:

// Legitimate call — real position
verify_transaction_inclusion_v2(ProofArgsV2 {
    tx_id: T2,
    tx_block_blockhash: block_hash,
    tx_index: 2,           // real last position
    merkle_proof: vec![T2, H(T0,T1)],
    coinbase_tx_id: T0,
    coinbase_merkle_proof: vec![H(T1,T1_pad), ...],  // real coinbase proof
    confirmations: 1,
});

// Attacker replay — phantom position N=3 (does not exist in the block)
verify_transaction_inclusion_v2(ProofArgsV2 {
    tx_id: T2,
    tx_block_blockhash: block_hash,
    tx_index: 3,           // phantom; no transaction at this index
    merkle_proof: vec![T2, H(T0,T1)],   // identical proof bytes
    coinbase_tx_id: T0,
    coinbase_merkle_proof: vec![H(T1,T1_pad), ...],  // same coinbase proof
    confirmations: 1,
});
// → returns true, bypassing the light-client oracle
```

The `compute_root_from_merkle_proof` trace for both calls is shown in the Finding Description above; both yield the same root because `H(T2, T2) == H(T2, T2)` regardless of whether the position is even (left-child) or odd (right-child) when both children are identical. [4](#0-3)

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
