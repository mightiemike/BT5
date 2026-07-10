### Title
Duplicate-Last-Leaf Phantom Index Bypass in Odd-Width Merkle Trees Allows Double-Spend — (`merkle-tools/src/lib.rs::compute_root_from_merkle_proof`)

---

### Summary

`compute_root_from_merkle_proof` performs no bounds check on `transaction_position` against the actual tree width. In a Bitcoin Merkle tree with an odd number of leaves, the last leaf is duplicated. This means the proof for the real last leaf (index `N-1`) produces an identical root when supplied with the phantom index `N`. An attacker can therefore call `verify_transaction_inclusion` twice for the same real transaction — once with the canonical index and once with the phantom index — and receive `true` both times. Any downstream bridge, mint, or unlock contract that uses `(tx_id, tx_index)` as its replay-protection key will process the same economic event twice.

---

### Finding Description

**Root cause — `compute_root_from_merkle_proof` (merkle-tools/src/lib.rs lines 34–52)**

The function iterates over the proof array, using `current_position % 2` to decide left/right placement, then divides by 2 each step:

```rust
for proof_hash in merkle_proof {
    if current_position % 2 == 0 {
        current_hash = compute_hash(&current_hash, proof_hash);
    } else {
        current_hash = compute_hash(proof_hash, &current_hash);
    }
    current_position /= 2;
}
``` [1](#0-0) 

There is no check that `transaction_position < tree_leaf_count`. The function never receives the tree width, so it cannot perform such a check.

**Why the phantom index produces the same root**

For a 3-transaction block `[T0, T1, T2]`, Bitcoin duplicates the last leaf:

```
Level 0 (padded): [T0, T1, T2, T2]
Level 1:          [H(T0,T1),  H(T2,T2)]
Root:              H(H(T0,T1), H(T2,T2))
```

The legitimate proof for `T2` at index `2` is `[T2, H(T0,T1)]`.

| Call | tx_id | tx_index | Step 1 | Step 2 | Result |
|------|-------|----------|--------|--------|--------|
| Legitimate | T2 | 2 (even) | `H(T2, T2)`, pos→1 | `H(H(T0,T1), H(T2,T2))`, pos→0 | **root** |
| Phantom | T2 | 3 (odd) | `H(T2, T2)`, pos→1 | `H(H(T0,T1), H(T2,T2))`, pos→0 | **root** |

Both calls return the same root. `verify_transaction_inclusion` compares this computed root against `header.block_header.merkle_root` and returns `true` for both. [2](#0-1) 

**`verify_transaction_inclusion` passes attacker-controlled `tx_index` directly**

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [3](#0-2) 

No guard exists between the caller-supplied `tx_index` and the Merkle computation. The only checks performed are confirmation depth and canonical-chain membership of the block hash — neither of which constrains the index. [4](#0-3) 

**`verify_transaction_inclusion_v2` is equally affected**

`verify_transaction_inclusion_v2` validates the coinbase proof at hardcoded index `0`, then delegates to `verify_transaction_inclusion` with the attacker-supplied `tx_index` unchanged:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [5](#0-4) 

The coinbase check does not constrain the target transaction's index.

**Reorg angle is not exploitable (correctly rejected)**

After a reorg, `reorg_chain` calls `remove_block_header` on displaced mainchain blocks, which removes them from `mainchain_header_to_height`. A subsequent call with a stale block hash panics at the canonical-chain membership check. The reorg timing race described in the question does not produce a bypass. [6](#0-5) 

---

### Impact Explanation

`verify_transaction_inclusion` is a pure view function; it stores no record of which `(tx_id, tx_index)` pairs have been verified. Replay protection is the caller's responsibility. Any downstream bridge or mint contract that keys its "already processed" set on `(tx_id, tx_index)` will accept the phantom-index call as a distinct, unprocessed event, allowing the same BTC deposit to be minted or unlocked twice.

---

### Likelihood Explanation

- The attacker needs only a confirmed Bitcoin block with an odd transaction count (extremely common — the majority of Bitcoin blocks have an odd number of transactions).
- The attacker needs the last transaction in that block to be a deposit or unlock event they wish to replay.
- No privileged role, relayer compromise, or social engineering is required. The function is publicly callable by any NEAR account.
- The proof bytes are identical for both calls; the only change is `tx_index` from `N-1` to `N`.

---

### Recommendation

Pass the total transaction count (`tx_count`) as a verified parameter to `compute_root_from_merkle_proof` and reject any `transaction_position >= tx_count` before computing the root. The `tx_count` must itself be committed to the block header or proven via a separate authenticated source; it cannot be caller-supplied without independent verification.

Alternatively, enforce that the proof length is consistent with the claimed index: `tx_index < 2^(merkle_proof.len())` is a necessary (though not sufficient) condition, and additionally require `tx_index < tx_count` where `tx_count` is authenticated.

---

### Proof of Concept

```rust
// 3-tx block: [T0, T1, T2]
// Legitimate proof for T2 at index 2 = [T2, H(T0,T1)]
// Phantom call with index 3 using the same proof:

let proof = vec![T2.clone(), hash_t0_t1.clone()];

// Call 1 — legitimate, index 2
let r1 = compute_root_from_merkle_proof(T2.clone(), 2, &proof);
// r1 == block_merkle_root  ✓

// Call 2 — phantom, index 3 (position does not exist in the block)
let r2 = compute_root_from_merkle_proof(T2.clone(), 3, &proof);
// r2 == block_merkle_root  ✓  ← same root, verify_transaction_inclusion returns true

assert_eq!(r1, r2); // passes — both equal the real Merkle root
```

A downstream bridge keying its replay set on `(tx_id, tx_index)` will process the deposit at index `2` and then again at index `3`, minting tokens twice for a single on-chain BTC deposit.

### Citations

**File:** merkle-tools/src/lib.rs (L42-49)
```rust
    for proof_hash in merkle_proof {
        if current_position % 2 == 0 {
            current_hash = compute_hash(&current_hash, proof_hash);
        } else {
            current_hash = compute_hash(proof_hash, &current_hash);
        }
        current_position /= 2;
    }
```

**File:** contract/src/lib.rs (L289-315)
```rust
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

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```

**File:** contract/src/lib.rs (L659-662)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```
