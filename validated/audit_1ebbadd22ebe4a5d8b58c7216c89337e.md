### Title
Odd-Leaf Duplicate Position Reuse Allows Same Transaction Verified at Two Distinct Indices — (`merkle-tools/src/lib.rs::compute_root_from_merkle_proof`)

---

### Summary

`compute_root_from_merkle_proof` performs no bounds check on `transaction_position` against the actual tree width. In any Bitcoin block whose Merkle tree has an odd leaf count, the last real transaction (index `N-1`) and its phantom duplicate slot (index `N`) both produce **identical proofs** that compute to the same valid root. `verify_transaction_inclusion_v2` passes both calls, allowing a downstream bridge/mint/withdrawal to be triggered twice for the same on-chain event.

---

### Finding Description

Bitcoin's Merkle tree construction pads an odd-length leaf row by duplicating the last leaf. For a 3-transaction block `[T0, T1, T2]`:

```
Padded leaves:  [T0,  T1,  T2,  T2]   (T2 duplicated at index 3)
Level 1:        [H(T0,T1),  H(T2,T2)]
Root:           H(H(T0,T1), H(T2,T2))
```

`compute_root_from_merkle_proof` only uses `current_position % 2` to decide left/right placement at each level: [1](#0-0) 

**Proof for T2 at real index 2 (even):**
- Step 1: pos=2 (even) → `H(T2, T2)`, pos→1
- Step 2: pos=1 (odd) → `H(H(T0,T1), H(T2,T2))` = Root ✓
- Proof: `[T2, H(T0,T1)]`

**Proof for T2 at phantom index 3 (odd):**
- Step 1: pos=3 (odd) → `H(T2, T2)`, pos→1
- Step 2: pos=1 (odd) → `H(H(T0,T1), H(T2,T2))` = Root ✓
- Proof: `[T2, H(T0,T1)]`

The proofs are **byte-for-byte identical** and both return the correct root. There is no guard anywhere in the call chain that rejects `tx_index >= actual_tx_count`.

`verify_transaction_inclusion_v2` adds a coinbase check at hardcoded position 0, which is independent of `tx_index` and provides no protection here: [2](#0-1) 

After the coinbase check passes, it delegates to `verify_transaction_inclusion` which passes `tx_index` directly into `compute_root_from_merkle_proof` with no upper-bound validation: [3](#0-2) 

The `ProofArgsV2 → ProofArgs` conversion passes `tx_index` through unchanged: [4](#0-3) 

---

### Impact Explanation

Any downstream bridge, mint, or withdrawal contract that:
1. Calls `verify_transaction_inclusion_v2` to confirm a BTC deposit/event, and
2. Tracks consumed proofs by `(tx_id, tx_index)` pair (a natural design to prevent replay)

...can be triggered **twice** for the same on-chain transaction: once with `tx_index = N-1` (real) and once with `tx_index = N` (phantom). Both calls return `true`. The attacker does not forge a nonexistent transaction — they replay a real one under a different index, bypassing the replay guard.

---

### Likelihood Explanation

Odd-transaction-count blocks are extremely common in Bitcoin (any block with an odd number of transactions triggers this). The attacker needs only:
- A real confirmed block in the light client's headers pool (publicly observable)
- The real coinbase proof for that block (derivable from public block data)
- The real proof for the last transaction (derivable from public block data)

No privileged role, no key compromise, no social engineering required. The call is fully permissionless via the public `verify_transaction_inclusion_v2` entrypoint. [5](#0-4) 

---

### Recommendation

In `compute_root_from_merkle_proof` (or at the call sites in `verify_transaction_inclusion`), validate that `transaction_position < (1 << merkle_proof.len())` — i.e., the claimed index must fit within the tree depth implied by the proof length. More precisely, the contract should reject any `tx_index` where `tx_index` and `tx_index ^ 1` (its sibling) would produce the same intermediate hash as the real last-leaf pair. The cleanest fix is to require `tx_index < 2^(proof.len() - 1)` when the proof's first element equals `tx_id` itself (the self-sibling pattern that signals a duplicated odd leaf).

---

### Proof of Concept

```
Block: 3 transactions [T0 (coinbase), T1, T2]
Merkle root = H(H(T0,T1), H(T2,T2))

// Call 1 — real position, accepted by bridge, marks (T2, index=2) as used
verify_transaction_inclusion_v2({
    tx_id:                 T2,
    tx_block_blockhash:    <real block hash>,
    tx_index:              2,
    merkle_proof:          [T2, H(T0,T1)],
    coinbase_tx_id:        T0,
    coinbase_merkle_proof: [T1, <...>],   // real coinbase proof
    confirmations:         0,
}) → true  ✓

// Call 2 — phantom position, SAME proof, bridge sees (T2, index=3) as unused
verify_transaction_inclusion_v2({
    tx_id:                 T2,
    tx_block_blockhash:    <real block hash>,
    tx_index:              3,             // phantom duplicate slot
    merkle_proof:          [T2, H(T0,T1)], // identical proof
    coinbase_tx_id:        T0,
    coinbase_merkle_proof: [T1, <...>],
    confirmations:         0,
}) → true  ✓  ← second acceptance of the same deposit
```

Both calls pass the coinbase check (same real coinbase proof) and both pass the Merkle root check (identical computation path). A bridge tracking `(tx_id, tx_index)` pairs processes the deposit twice.

### Citations

**File:** merkle-tools/src/lib.rs (L39-51)
```rust
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

**File:** contract/src/lib.rs (L347-347)
```rust
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
```

**File:** contract/src/lib.rs (L358-368)
```rust
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

**File:** btc-types/src/contract_args.rs (L38-47)
```rust
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
