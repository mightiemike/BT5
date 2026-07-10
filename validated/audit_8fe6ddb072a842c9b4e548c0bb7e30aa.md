### Title
`tx_index` Aliasing in `compute_root_from_merkle_proof` Allows Out-of-Range Index to Return `true` — (`merkle-tools/src/lib.rs`)

---

### Summary

`compute_root_from_merkle_proof` uses only the low-order bits of `transaction_position` at each tree level. For a proof of length N, any index `k + 2^N` produces **exactly the same hash traversal** as index `k`. Because neither `verify_transaction_inclusion` nor `verify_transaction_inclusion_v2` bounds-checks `tx_index` against the actual number of transactions in the block, an unprivileged caller can supply an out-of-range index and receive `true`.

---

### Finding Description

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` iterates over the proof, consuming one bit of `current_position` per level:

```rust
for proof_hash in merkle_proof {
    if current_position % 2 == 0 { ... } else { ... }
    current_position /= 2;
}
```

After N iterations the loop ends. The only information consumed from `current_position` is the N least-significant bits. Any two indices that share the same N LSBs — i.e., `k` and `k + 2^N` — produce an **identical** computation and therefore an identical returned root. [1](#0-0) 

`verify_transaction_inclusion` passes `args.tx_index` directly to `compute_root_from_merkle_proof` with no upper-bound check. The contract stores only block headers, not transaction counts, so there is no stored value to compare against. [2](#0-1) 

`verify_transaction_inclusion_v2` adds a coinbase proof check (hardcoded to index `0`) but then delegates to `verify_transaction_inclusion` unchanged, so the same unchecked `tx_index` flows through. [3](#0-2) 

---

### Impact Explanation

Any NEAR contract or off-chain consumer that calls `verify_transaction_inclusion` (or `_v2`) to confirm a BTC transaction is included in a block can be deceived. An attacker who knows a real transaction at index `k` in a block with a depth-N Merkle tree can claim the same transaction exists at index `k + 2^N` — a position that does not exist — and the contract returns `true`. This allows a non-existent BTC transaction position to be treated as confirmed, which is the exact Critical impact in scope.

---

### Likelihood Explanation

The entrypoint is a public, unpermissioned NEAR contract call (`#[pause]` only, no role restriction). The attacker needs only:
1. A real transaction at index `k` in any canonical block with sufficient confirmations (publicly observable on-chain).
2. The valid Merkle proof for that transaction (computable from public block data).
3. To submit `tx_index = k + 2^N` instead of `k`.

No privileged role, leaked key, or social engineering is required.

---

### Recommendation

Add an explicit upper-bound check before calling `compute_root_from_merkle_proof`. Since the contract stores only headers (not tx counts), the bound can be derived from the proof length: a valid leaf index for a proof of length N must satisfy `tx_index < 2^N`. Reject any `tx_index >= 2^(merkle_proof.len())`:

```rust
require!(
    args.tx_index < (1u64 << args.merkle_proof.len()),
    "tx_index out of range for the given proof depth"
);
```

This is a necessary but not sufficient fix; ideally the block's transaction count would also be stored and checked.

---

### Proof of Concept

For a 4-transaction block (N = 2 proof levels), compute the valid proof for index 0, then call with `tx_index = 4`:

```rust
#[test]
fn test_index_aliasing() {
    let tx_hashes = vec![H0, H1, H2, H3]; // 4 real txs
    let root = merkle_root_calculator(&tx_hashes);
    let proof_for_0 = merkle_proof_calculator(tx_hashes.clone(), 0); // len = 2, N = 2

    // Valid call: index 0
    let r0 = compute_root_from_merkle_proof(H0, 0, &proof_for_0);
    assert_eq!(r0, root);

    // Aliased call: index 4 = 0 + 2^2 — does NOT exist in a 4-tx block
    let r4 = compute_root_from_merkle_proof(H0, 4, &proof_for_0);
    assert_eq!(r4, root); // passes — verify_transaction_inclusion returns true for index 4
}
```

At step 0: `(4 % 2) == (0 % 2) == 0` → same branch. `4/2 = 2`, `0/2 = 0`.
At step 1: `(2 % 2) == (0 % 2) == 0` → same branch. Loop ends.
Both paths are identical; the returned root matches `header.block_header.merkle_root`, so `verify_transaction_inclusion` returns `true` for the non-existent index 4. [4](#0-3)

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
