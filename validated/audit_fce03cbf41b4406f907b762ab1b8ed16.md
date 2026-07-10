### Title
`tx_index` Upper-Bit Truncation in `compute_root_from_merkle_proof` Allows Proof Replay at Phantom Indices — (`merkle-tools/src/lib.rs`)

---

### Summary

`compute_root_from_merkle_proof` only consumes the lowest `merkle_proof.len()` bits of `transaction_position`. Any bits at position `k` or higher (where `k = merkle_proof.len()`) are right-shifted away before they can influence the hash-ordering decision. As a result, `verify_transaction_inclusion` returns `true` for any `tx_index` of the form `real_index + n·2^k`, even though no such transaction exists at that position in the block.

---

### Finding Description

The loop in `compute_root_from_merkle_proof` runs exactly `merkle_proof.len()` times:

```rust
for proof_hash in merkle_proof {
    if current_position % 2 == 0 {          // reads bit 0
        current_hash = compute_hash(&current_hash, proof_hash);
    } else {
        current_hash = compute_hash(proof_hash, &current_hash);
    }
    current_position /= 2;                  // discards bit 0, shifts right
}
``` [1](#0-0) 

Each iteration reads bit 0 of `current_position` and then discards it via integer division. After `k` iterations the loop ends. Bits at positions `k, k+1, k+2, …` of the original `transaction_position` are never read. Therefore:

```
compute_root_from_merkle_proof(hash, index,       proof) ==
compute_root_from_merkle_proof(hash, index + 2^k, proof)   // for any k >= proof.len()
```

`verify_transaction_inclusion` passes `args.tx_index` directly to this function and compares the returned hash to the stored merkle root:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [2](#0-1) 

There is no guard of the form `args.tx_index < (1 << args.merkle_proof.len())`. The same flaw is inherited by `verify_transaction_inclusion_v2`, which delegates to `verify_transaction_inclusion` after its coinbase check:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [3](#0-2) 

---

### Impact Explanation

The light client's contract guarantees that a `true` return means "transaction `tx_id` is at position `tx_index` in the block." That guarantee is broken. An attacker who holds a valid proof for `(tx_id, real_index)` can call the contract with `tx_index = real_index + 2^k` (or `+ 2·2^k`, `+ 3·2^k`, …) and receive `true`. Any consuming contract that uses the caller-supplied `tx_index` to identify which Bitcoin output, event, or UTXO to act on will process a different index than the one actually proven, while the light client has certified the claim as valid.

---

### Likelihood Explanation

Both `verify_transaction_inclusion` (public, deprecated but still callable) and `verify_transaction_inclusion_v2` (public, current) are reachable by any NEAR account without any privileged role. The attacker only needs a legitimately obtained SPV proof for a real transaction; no chain manipulation, key compromise, or social engineering is required. The arithmetic property is deterministic and requires no special timing or race condition. [4](#0-3) 

---

### Recommendation

Add a bounds check at the top of `compute_root_from_merkle_proof` (or inside `verify_transaction_inclusion` before the call):

```rust
assert!(
    transaction_position < (1usize << merkle_proof.len()),
    "tx_index exceeds the address space of the supplied proof depth"
);
```

This ensures that every bit of `transaction_position` is actually consumed by the loop, making the index-to-root mapping injective.

---

### Proof of Concept

The following self-contained Rust unit test (add to `merkle-tools/src/lib.rs` under `#[cfg(test)]`) demonstrates the invariant break:

```rust
#[test]
fn test_index_upper_bits_ignored() {
    // 4-leaf tree → proof depth k = 2
    let leaves: Vec<H256> = (0u8..4)
        .map(|i| double_sha256(&[i]))
        .collect();

    let root = merkle_root_calculator(&leaves);
    let proof = merkle_proof_calculator(leaves.clone(), 1); // proof for index 1
    assert_eq!(proof.len(), 2);                             // k = 2

    // index 1, 5 (=1+4), 9 (=1+8), 13 (=1+12) all produce the same root
    for &idx in &[1usize, 5, 9, 13] {
        assert_eq!(
            compute_root_from_merkle_proof(leaves[1].clone(), idx, &proof),
            root,
            "index {} should NOT match root but does", idx
        );
    }
}
```

All four assertions pass on the unmodified codebase, confirming that `verify_transaction_inclusion` returns `true` for `tx_index` values 1, 5, 9, and 13 with the same proof — even though only index 1 is a real position in the 4-transaction block. [5](#0-4)

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

**File:** btc-types/src/contract_args.rs (L18-24)
```rust
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
