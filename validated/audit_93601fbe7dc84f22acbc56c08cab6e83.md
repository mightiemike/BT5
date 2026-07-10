### Title
Duplicate-Last-Leaf Position Aliasing in `verify_transaction_inclusion_v2` Allows Same Proof to Verify at Two Distinct `tx_index` Values — (`merkle-tools/src/lib.rs::compute_root_from_merkle_proof`)

---

### Summary

`compute_root_from_merkle_proof` performs no bounds check on `transaction_position`. In a Bitcoin Merkle tree with an odd number of leaves, the last leaf is duplicated to form a pair. This means the same `(tx_id, merkle_proof)` tuple produces the correct Merkle root for **two distinct `tx_index` values** — the real last-leaf position `N` and the virtual duplicate position `N+1`. The coinbase-proof guard added in `verify_transaction_inclusion_v2` does not prevent this: it only confirms the coinbase is in the block; it does not bind `tx_index` to a unique, in-bounds position. Any downstream bridge that calls `verify_transaction_inclusion_v2` and tracks processed deposits by `tx_id` alone (or by `(tx_id, tx_index)` without bounding `tx_index`) can be double-spent.

---

### Finding Description

**Root cause — `compute_root_from_merkle_proof`** (`merkle-tools/src/lib.rs`, lines 34–52):

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

The function is purely arithmetic on `current_position % 2` at each level. It never checks whether `transaction_position` is within the actual number of transactions in the block.

**Why the duplicate-leaf aliasing works:**

Consider a block with 5 transactions `[T0, T1, T2, T3, T4]`. Bitcoin's Merkle tree pads odd-width levels by duplicating the last element:

```
Level 0 (padded): [T0,  T1,  T2,  T3,  T4,  T4 ]   ← T4 duplicated at index 5
Level 1 (padded): [H01, H23, H44, H44]               ← H44=H(T4,T4) duplicated at index 3
Level 2:          [H0123, H4444]                      ← H4444=H(H44,H44)
Root:              H(H0123, H4444)
```

The canonical proof for T4 at position 4 is:
```
proof[0] = T4      (sibling at level 0, position 5)
proof[1] = H44     (sibling at level 1, position 3)
proof[2] = H0123   (sibling at level 2, position 0)
```

**Verification at position 4 (real):**
```
pos=4 (even): H(T4, T4)   = H44    → pos=2
pos=2 (even): H(H44, H44) = H4444  → pos=1
pos=1 (odd):  H(H0123, H4444)      = Root ✓
```

**Verification at position 5 (virtual duplicate), same proof, same tx_id:**
```
pos=5 (odd):  H(T4, T4)   = H44    → pos=2
pos=2 (even): H(H44, H44) = H4444  → pos=1
pos=1 (odd):  H(H0123, H4444)      = Root ✓
```

Both calls return the same root. `verify_transaction_inclusion_v2` returns `true` for both.

**Why the coinbase guard does not help:**

`verify_transaction_inclusion_v2` adds:

```rust
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
```

This only confirms that the coinbase transaction is in the block. It does not:
- Validate that `args.tx_index` is less than the actual transaction count
- Prevent the same `(tx_id, proof)` from satisfying two different `tx_index` values
- Bind `tx_index` to a unique leaf position

The block header stores only the Merkle root — not the transaction count — so the contract has no in-band way to bound `tx_index`. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

A downstream bridge contract that calls `verify_transaction_inclusion_v2` to gate minting or unlocking:

1. **Double-spend via position aliasing:** A real deposit transaction T4 at position 4 in a 5-tx block can be claimed at position 4 (legitimate) and again at position 5 (fraudulent). If the bridge deduplicates by `tx_id` alone, the second call is blocked. If it deduplicates by `(tx_id, tx_index)`, the second call succeeds and a second mint/unlock executes for a transaction that funded the bridge only once.

2. **False deposit for a non-deposit transaction:** Any transaction that happens to be the last in an odd-width block can be presented at the virtual duplicate index `N+1`. The light client returns `true`. If the bridge parses the transaction content from the proof rather than from a trusted source, it may accept a non-deposit transaction as a deposit.

The light client itself does not track used proofs; that responsibility falls to callers. But the light client's broken invariant — that a proof binds to exactly one position — makes it impossible for callers to safely deduplicate by `(tx_id, tx_index)` alone. [4](#0-3) 

---

### Likelihood Explanation

- `verify_transaction_inclusion_v2` is a public, unpermissioned NEAR contract call. No role, stake, or trusted-relayer check gates it.
- Odd-width Bitcoin blocks are common (any block with an odd number of transactions triggers the duplication).
- The attacker only needs to know the canonical proof for the last transaction in any odd-width block — this is public information derivable from the block's transaction list.
- No special chain state is required; the attack works against any confirmed block already stored in the light client. [5](#0-4) 

---

### Recommendation

1. **Pass the transaction count through the proof or a separate argument.** The block header does not store the transaction count, but the coinbase transaction's `vin[0].coinbase` scriptSig encodes the block height (BIP34). A separate `tx_count` field in `ProofArgsV2` — verified against the coinbase proof depth — would allow bounding `tx_index < tx_count`.

2. **Reject `tx_index >= (1 << proof_length)` as a soft guard.** A proof of length `k` can only address positions `0..(2^k - 1)`. Positions at or beyond the padded tree width are virtual duplicates. Specifically, if `tx_index >= (1 << merkle_proof.len())`, the position is out of range and the call should be rejected.

3. **Alternatively, require `tx_index` to be strictly less than the number of leaves implied by the proof.** For a proof of length `k`, the maximum valid index is `2^k - 1`, but the actual last valid index depends on the real transaction count. Without the transaction count, the minimal safe guard is to reject any `tx_index` that equals its sibling (i.e., `tx_index` is even and `proof[0] == tx_id`, which is the signature of a duplicate-leaf proof).

---

### Proof of Concept

Concrete call sequence against an unmodified contract (pseudocode, executable as a NEAR integration test):

```
// Block has 5 transactions: [coinbase=C, T1, T2, T3, T4]
// T4 is the last transaction; the tree duplicates it.
// Canonical proof for T4 at position 4:
//   proof = [T4, H(T4,T4), H(H(C,T1),H(T2,T3))]

// Call 1 — legitimate, position 4:
verify_transaction_inclusion_v2({
    tx_id:                 T4,
    tx_block_blockhash:    <confirmed block hash>,
    tx_index:              4,
    merkle_proof:          [T4, H(T4,T4), H(H(C,T1),H(T2,T3))],
    coinbase_tx_id:        C,
    coinbase_merkle_proof: [T1, H(T2,T3), H(H(T4,T4),H(T4,T4))],
    confirmations:         1,
})
// → returns true  ✓ (expected)

// Call 2 — fraudulent, position 5 (virtual duplicate, no real tx):
verify_transaction_inclusion_v2({
    tx_id:                 T4,
    tx_block_blockhash:    <same confirmed block hash>,
    tx_index:              5,          // ← only change
    merkle_proof:          [T4, H(T4,T4), H(H(C,T1),H(T2,T3))],  // ← identical proof
    coinbase_tx_id:        C,
    coinbase_merkle_proof: [T1, H(T2,T3), H(H(T4,T4),H(T4,T4))],
    confirmations:         1,
})
// → returns true  ✗ (position 5 does not exist; no real transaction funded the bridge here)
```

Both calls return `true`. A bridge that mints on each `true` result and deduplicates by `(tx_id, tx_index)` will execute two mints for a single on-chain deposit. [6](#0-5) [7](#0-6)

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

**File:** contract/src/lib.rs (L315-323)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

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

**File:** btc-types/src/contract_args.rs (L28-36)
```rust
pub struct ProofArgsV2 {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub coinbase_tx_id: H256,
    pub coinbase_merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
