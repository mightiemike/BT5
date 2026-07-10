I have all the code needed. Let me trace the math precisely before writing the finding.

**5-tx tree [A,B,C,D,E] — proof path trace:**

`merkle_proof_calculator` at index 4 (real E):
- L0: [A,B,C,D,E] → odd → pad to [A,B,C,D,E,**E**]; pos=4 even → sibling = `E`; advance → [h(A,B), h(C,D), h(E,E)], pos=2
- L1: [h(A,B),h(C,D),h(E,E)] → odd → pad; pos=2 even → sibling = `h(E,E)`; advance → [h(h(A,B),h(C,D)), h(h(E,E),h(E,E))], pos=1
- L2: pos=1 odd → sibling = `h(h(A,B),h(C,D))`
- **Proof₄ = [E, h(E,E), h(h(A,B),h(C,D))]**

`merkle_proof_calculator` at index 5 (phantom):
- L0: same pad; pos=5 **odd** → sibling = `E`; advance → same level-1 hashes, pos=2
- L1: same pad; pos=2 even → sibling = `h(E,E)`; advance, pos=1
- L2: pos=1 odd → sibling = `h(h(A,B),h(C,D))`
- **Proof₅ = [E, h(E,E), h(h(A,B),h(C,D))]** — identical to Proof₄

`compute_root_from_merkle_proof(E, 5, Proof₅)`:
- pos=5 odd → `h(E,E)`, pos=2
- pos=2 even → `h(h(E,E),h(E,E))`, pos=1
- pos=1 odd → `h(h(h(A,B),h(C,D)), h(h(E,E),h(E,E)))` = **merkle_root** ✓

The math is confirmed. Now assessing the full production path and impact:

---

### Title
Phantom-Index Merkle Proof Forgery via Odd-Level Duplication Asymmetry — (`merkle-tools/src/lib.rs`, `contract/src/lib.rs`)

### Summary
`compute_root_from_merkle_proof` has no knowledge of tree size. When a Bitcoin block has a transaction count that produces an odd number of nodes at one or more intermediate tree levels, the last-node duplication applied by `merkle_proof_calculator` creates identical proof vectors for the real last position and its phantom mirror. An attacker can supply `tx_index = N` (out-of-bounds phantom) with the same proof as `tx_index = N-1` (real) and receive `true` from both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2`.

### Finding Description

`merkle_proof_calculator` pads odd-length levels by duplicating the last element before computing sibling hashes: [1](#0-0) 

`compute_root_from_merkle_proof` is purely positional — it uses only `current_position % 2` to decide left/right at each step, with no awareness of tree width: [2](#0-1) 

For a 5-tx tree, the proof for real index 4 and phantom index 5 are byte-for-byte identical (`[E, h(E,E), h(h(A,B),h(C,D))]`). When `compute_root_from_merkle_proof` processes index 5 with this proof, the parity sequence `(5%2=1, 2%2=0, 1%2=1)` produces the same hash chain as index 4 `(4%2=0, 2%2=0, 1%2=1)` because both converge to `h(E,E)` at step 1 — index 4 via `hash(E, proof[0]=E)` and index 5 via `hash(proof[0]=E, E)`. The result equals the canonical merkle root.

`verify_transaction_inclusion` passes the attacker-supplied `tx_index` directly to `compute_root_from_merkle_proof` with no bounds check against the actual transaction count (which is not stored in the block header): [3](#0-2) 

`verify_transaction_inclusion_v2` adds a coinbase proof length-equality check and a coinbase root check, but neither prevents this attack. The coinbase proof for a 5-tx block has depth 3, the phantom tx proof also has depth 3 (lengths match), and the coinbase proof itself is valid: [4](#0-3) 

### Impact Explanation

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` return `true` for a `tx_index` that does not correspond to any real position in the block's transaction list. Any downstream protocol that gates an action on the result of these calls (e.g., cross-chain bridge unlock, payment settlement, SPV relay) can be triggered with a phantom index claim. The scope definition explicitly lists "wrong index" as a Critical impact, and this is a concrete, mechanically provable instance of it.

The attack does require the attacker to supply a real transaction hash (`E`) that genuinely appears in the block — they cannot forge a completely invented txid. However, they can claim that transaction is at a position that does not exist, which breaks the inclusion-position invariant the contract is supposed to enforce.

### Likelihood Explanation

Any Bitcoin block whose transaction count produces an odd number of nodes at any intermediate tree level is vulnerable. This is extremely common — any block with 3, 5, 6 (level-1 odd after pairing), 9, 10, 11, 12 transactions, etc. The attacker needs only to observe a qualifying block on-chain, extract the real proof for the last transaction in an odd-length subtree, and submit it with `tx_index = real_index + 1`. No privileged access, no key material, no social engineering required. Both the deprecated v1 and the current v2 public NEAR contract methods are reachable by any caller.

### Recommendation

1. **Enforce proof depth against tree size.** The number of proof elements must equal `ceil(log2(tx_count))`. Since the block header does not store `tx_count`, callers must supply it and the contract must validate `merkle_proof.len() == expected_depth(tx_count)` and `tx_index < tx_count`.
2. **Alternatively**, reject any `tx_index` where `tx_index >= (1 << merkle_proof.len())` as a loose upper-bound guard (this does not fully fix the issue but eliminates the simplest phantom cases).
3. The `verify_transaction_inclusion_v2` coinbase-length equality check (`merkle_proof.len() == coinbase_merkle_proof.len()`) does not substitute for a tree-size check and should not be treated as one.

### Proof of Concept

```rust
#[test]
fn test_phantom_index_forgery_5tx() {
    use merkle_tools::{merkle_proof_calculator, compute_root_from_merkle_proof};

    // Minimal distinct hashes
    let make = |b: u8| -> H256 { H256([b; 32]) };
    let txs = vec![make(1), make(2), make(3), make(4), make(5)]; // [A,B,C,D,E]

    // Compute canonical root via the reference calculator
    let real_proof   = merkle_proof_calculator(txs.clone(), 4); // real index of E
    let phantom_proof = merkle_proof_calculator(txs.clone(), 5); // phantom

    // Both proof vectors must be identical
    assert_eq!(real_proof, phantom_proof, "proofs differ — precondition failed");

    // Both must produce the canonical root
    let root_real    = compute_root_from_merkle_proof(make(5), 4, &real_proof);
    let root_phantom = compute_root_from_merkle_proof(make(5), 5, &phantom_proof);

    assert_eq!(root_real, root_phantom,
        "phantom index 5 produces the same root as real index 4");

    // Confirm this equals the actual merkle root of the 5-tx tree
    // (root_real is the canonical root by construction from merkle_proof_calculator)
    // verify_transaction_inclusion would return `true` for tx_index=5
}
``` [5](#0-4) [6](#0-5)

### Citations

**File:** merkle-tools/src/lib.rs (L4-31)
```rust
pub fn merkle_proof_calculator(tx_hashes: Vec<H256>, transaction_position: usize) -> Vec<H256> {
    let mut transaction_position = transaction_position;
    let mut merkle_proof = Vec::new();
    let mut current_hashes = tx_hashes;

    while current_hashes.len() > 1 {
        if current_hashes.len() % 2 == 1 {
            current_hashes.push(current_hashes[current_hashes.len() - 1].clone());
        }

        if transaction_position % 2 == 1 {
            merkle_proof.push(current_hashes[transaction_position - 1].clone());
        } else {
            merkle_proof.push(current_hashes[transaction_position + 1].clone());
        }

        let mut new_hashes = Vec::new();

        for i in (0..current_hashes.len() - 1).step_by(2) {
            new_hashes.push(compute_hash(&current_hashes[i], &current_hashes[i + 1]));
        }

        current_hashes = new_hashes;
        transaction_position /= 2;
    }

    merkle_proof
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

**File:** contract/src/lib.rs (L317-322)
```rust
        // compute merkle tree root and check if it matches block's original merkle tree root
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
