Let me examine the exact proof logic and the `ProofArgs` structure to complete the analysis.

The mathematical analysis is complete. Let me trace through the exact proof arithmetic.

---

### Title
`compute_root_from_merkle_proof` accepts phantom `tx_index` in odd-width trees, enabling double-spend via duplicate-last-leaf position aliasing — (`merkle-tools/src/lib.rs`)

---

### Summary

`compute_root_from_merkle_proof` performs no bounds check on `transaction_position`. In any Bitcoin block whose transaction count is odd, the last leaf is duplicated during Merkle tree construction. This causes the valid proof for the last real transaction at position `N-1` to produce an identical root when supplied with position `N` (a phantom that does not exist in the block). `verify_transaction_inclusion` therefore returns `true` for `(tx_id=T_{N-1}, tx_index=N)` — a position that was never in the block — allowing a downstream bridge to be tricked into accepting the same deposit twice.

---

### Finding Description

**`merkle_proof_calculator` duplication (reference only):** [1](#0-0) 

When the leaf count is odd, the last leaf is appended as its own sibling. This is standard Bitcoin behaviour.

**`compute_root_from_merkle_proof` — no position bounds check:** [2](#0-1) 

The function only uses `current_position % 2` to decide left/right ordering and `current_position /= 2` to ascend. It never validates that `transaction_position < tree_leaf_count`.

**Concrete arithmetic for a 3-tx tree `[T0, T1, T2]`:**

```
After duplication:  [T0, T1, T2, T2]
Level 1:            [H(T0,T1),  H(T2,T2)]
Root:               H(H(T0,T1), H(T2,T2))

Proof for T2 at position 2:  [T2,  H(T0,T1)]

Verify at position 2:
  step 1: pos=2, 2%2==0 → hash(T2, T2)   = H22;  pos=1
  step 2: pos=1, 1%2==1 → hash(H(T0,T1), H22) = Root  ✓

Verify SAME proof at position 3 (phantom):
  step 1: pos=3, 3%2==1 → hash(T2, T2)   = H22;  pos=1
  step 2: pos=1, 1%2==1 → hash(H(T0,T1), H22) = Root  ✓
```

Both calls return `Root`. The function is indistinguishable between the real position and the phantom.

**`verify_transaction_inclusion` passes the attacker-supplied `tx_index` directly:** [3](#0-2) 

No guard exists between `args.tx_index` and `compute_root_from_merkle_proof`. The only checks are confirmations and canonical-chain membership. [4](#0-3) 

**`verify_transaction_inclusion_v2` does not close the gap:**

The coinbase check verifies position `0` with a proof of the same *length* as the target proof. [5](#0-4) 

For the 3-tx example the coinbase proof depth is 2 and the phantom-position proof depth is also 2, so the length-equality guard passes. The coinbase check anchors tree depth but not the upper bound of valid leaf indices. After the coinbase check passes, `verify_transaction_inclusion` is called with the unvalidated `tx_index=N`.

**`ProofArgs.tx_index` is fully attacker-controlled:** [6](#0-5) 

---

### Impact Explanation

The light client returns `true` for `(tx_id=T_{N-1}, tx_index=N)` where `N` is a phantom position. A downstream bridge contract that uses `(tx_id, tx_index)` as its uniqueness key for tracking redeemed deposits will accept the same Bitcoin deposit twice:

1. Attacker makes a real Bitcoin deposit → tx lands at position `N-1` in an odd-width block.
2. Bridge call 1: `verify_transaction_inclusion(tx_id=T_{N-1}, tx_index=N-1, proof=[T_{N-1}, …])` → `true` → bridge mints/unlocks.
3. Bridge call 2: `verify_transaction_inclusion(tx_id=T_{N-1}, tx_index=N, proof=[T_{N-1}, …])` → `true` → bridge mints/unlocks again.

The second call is for a position that never existed; the light client cannot distinguish it from a legitimate proof. The attacker receives double the bridged value from a single on-chain Bitcoin deposit.

---

### Likelihood Explanation

- Odd transaction counts are the norm in Bitcoin blocks (most real blocks have an odd number of transactions).
- The attacker needs only one real deposit in such a block; no privileged role, no relayer compromise, no key leak is required.
- Both `verify_transaction_inclusion` (public, deprecated but not removed) and `verify_transaction_inclusion_v2` (public, current) are reachable by any NEAR account.
- The proof construction is trivial: take the canonical proof for position `N-1` and submit it unchanged with `tx_index=N`.

---

### Recommendation

1. **In `compute_root_from_merkle_proof`**: accept an explicit `tree_leaf_count: usize` parameter and assert `transaction_position < tree_leaf_count` before the loop. Reject any proof where `transaction_position >= tree_leaf_count`.
2. **In `verify_transaction_inclusion` / `_v2`**: require callers to supply the transaction count for the block, or store it in `ExtendedHeader`, and pass it to the proof verifier.
3. **Downstream bridges**: use `tx_id` alone (not `(tx_id, tx_index)`) as the uniqueness key, which eliminates the double-spend surface even if the light client is not patched immediately.

---

### Proof of Concept

```rust
// 3-tx tree: [T0, T1, T2]
// Proof for T2 at position 2 (real): [T2, H(T0,T1)]
// Same proof submitted at position 3 (phantom):

let proof = vec![T2.clone(), hash_T0_T1.clone()];

// Call 1 — legitimate
assert!(compute_root_from_merkle_proof(T2.clone(), 2, &proof) == root);

// Call 2 — phantom position, same proof, same result
assert!(compute_root_from_merkle_proof(T2.clone(), 3, &proof) == root);

// Both calls to verify_transaction_inclusion return true.
// A bridge keyed on (tx_id, tx_index) mints twice.
```

The `test_merkle_proof_verification_odd` test in `merkle-tools/src/lib.rs` already exercises a 5-tx odd tree at position 4 (the last real leaf) but never tests position 5 (the phantom), confirming the gap is untested. [7](#0-6)

### Citations

**File:** merkle-tools/src/lib.rs (L9-11)
```rust
    while current_hashes.len() > 1 {
        if current_hashes.len() % 2 == 1 {
            current_hashes.push(current_hashes[current_hashes.len() - 1].clone());
```

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

**File:** merkle-tools/src/lib.rs (L154-173)
```rust
    #[test]
    fn test_merkle_proof_verification_odd() {
        let tx_hashes = vec![
            decode_hex("18afbf37d136ff62644b231fcde72f1fb8edd04a798fb00cb06360da635da275"),
            decode_hex("30b19832a5f4b952e151de77d96139987492becc8b6e1e914c4103cfbb06c01e"),
            decode_hex("b94ed12902e35b29dd53cf25e665b4d0bc92f22adbc383ad90566584902b061d"),
            decode_hex("1920e5d8a10018dc65308bb4d1f11d30b5406c6499688443bfcd1ef364206b14"),
            decode_hex("048f3897c16bdc59ec1187aa080a4b4aa5ec1afcb4b776cf8b8a214b01990a7b"),
        ];

        let calculated_merkle_root = merkle_root_calculator(&tx_hashes);
        let calculated_merkle_proof = merkle_proof_calculator(tx_hashes, 4);

        let computed_root_from_merkle_proof = compute_root_from_merkle_proof(
            decode_hex("048f3897c16bdc59ec1187aa080a4b4aa5ec1afcb4b776cf8b8a214b01990a7b"),
            4,
            &calculated_merkle_proof,
        );
        assert_eq!(computed_root_from_merkle_proof, calculated_merkle_root);
    }
```

**File:** contract/src/lib.rs (L288-323)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
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

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
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
