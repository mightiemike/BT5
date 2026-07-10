### Title
Incomplete Coinbase Anchor in `verify_transaction_inclusion_v2` Allows Internal-Node Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` was introduced to mitigate the 64-byte Merkle second-preimage attack by requiring a coinbase proof. However, the coinbase check only verifies that `compute_root_from_merkle_proof(coinbase_tx_id, 0, &coinbase_merkle_proof) == merkle_root`. It does **not** verify that `coinbase_tx_id` is the actual leaf at position 0 (the real coinbase transaction). An attacker can supply an internal Merkle node as `coinbase_tx_id` with a shorter proof that still reconstructs the root, then use the same (or a sibling) internal node as `tx_id` to obtain a `true` return value for a non-existent transaction.

---

### Finding Description

The guard in `verify_transaction_inclusion_v2` is:

```
require!(
    merkle_proof.len() == coinbase_merkle_proof.len(),
    ...
);
require!(
    compute_root_from_merkle_proof(coinbase_tx_id, 0, &coinbase_merkle_proof) == merkle_root,
    "Incorrect coinbase merkle proof"
);
``` [1](#0-0) 

`compute_root_from_merkle_proof` is a pure hash-chain function with no leaf/internal-node distinction:

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
``` [2](#0-1) 

**Concrete exploit for a 4-transaction block** (`T0, T1, T2, T3`):

```
         Root
        /    \
      N01    N23
     /  \   /  \
    T0  T1 T2  T3
```

- `N01 = hash(T0, T1)`, `N23 = hash(T2, T3)`, `Root = hash(N01, N23)`

**Attacker supplies:**
- `coinbase_tx_id = N01` (internal node, NOT the real coinbase leaf T0)
- `coinbase_merkle_proof = [N23]` (length 1)
- `compute_root_from_merkle_proof(N01, 0, [N23])` = `hash(N01, N23)` = `Root` ✓ — coinbase check passes

**Length constraint satisfied:** `coinbase_merkle_proof.len() == merkle_proof.len() == 1`

**Then for the tx proof:**
- `tx_id = N23`, `tx_index = 1`, `merkle_proof = [N01]`
- `compute_root_from_merkle_proof(N23, 1, [N01])` = `hash(N01, N23)` = `Root` ✓

`verify_transaction_inclusion` returns `true` for `tx_id = N23`, which is an internal node, not a real transaction. [3](#0-2) 

The `verify_transaction_inclusion` call (invoked via `args.into()`) performs no additional leaf-vs-internal-node check — it only recomputes the Merkle root and compares.

---

### Impact Explanation

An attacker can cause `verify_transaction_inclusion_v2` to return `true` for a `tx_id` that is an internal Merkle node, not a real Bitcoin transaction. Any downstream system that trusts this return value to confirm a payment or cross-chain event can be deceived. This is the exact class of forgery the v2 function was designed to prevent; the mitigation is bypassed.

The attacker is constrained to internal nodes that exist in the block's Merkle tree at the same depth as the fake coinbase — they cannot forge a completely arbitrary 32-byte hash. However, internal nodes are publicly computable from any block, so no privileged access is required.

---

### Likelihood Explanation

- The block's Merkle tree is fully public; all internal nodes are trivially computable.
- The call is a public NEAR view/change method with no access control.
- The attacker only needs to know the target block's transaction list, which is on-chain Bitcoin data.
- No key compromise, social engineering, or privileged role is required.

---

### Recommendation

After the coinbase proof passes, add an explicit check that `coinbase_tx_id` is the actual coinbase leaf — i.e., that `coinbase_merkle_proof.len()` equals the full tree depth (same as `merkle_proof.len()` is already enforced, but the tree depth must equal the full depth for a leaf proof). Concretely, require that the coinbase proof length equals `ceil(log2(tx_count))`, or equivalently, require that the caller also supplies the raw coinbase transaction bytes and verify `double_sha256(raw_coinbase) == coinbase_tx_id`. Without knowing the transaction count on-chain, the most robust fix is to require the raw coinbase bytes and hash them on-chain before using the result as the anchor.

---

### Proof of Concept

```rust
// Block with 4 txs: T0 (coinbase), T1, T2, T3
// N01 = hash(T0, T1), N23 = hash(T2, T3), Root = hash(N01, N23)

// Attacker call to verify_transaction_inclusion_v2:
ProofArgsV2 {
    tx_id: N23,                  // internal node — NOT a real transaction
    tx_block_blockhash: block_hash,
    tx_index: 1,
    merkle_proof: vec![N01],     // length 1
    coinbase_tx_id: N01,         // internal node — NOT the real coinbase T0
    coinbase_merkle_proof: vec![N23], // length 1 — passes length check
    confirmations: 0,
}
// compute_root_from_merkle_proof(N01, 0, [N23]) == Root  → coinbase check passes
// compute_root_from_merkle_proof(N23, 1, [N01]) == Root  → tx inclusion returns true
// Result: true  — for a non-existent transaction
``` [4](#0-3) [2](#0-1)

### Citations

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
