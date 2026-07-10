### Title
Unvalidated `tx_index` Allows Proof Verification with Non-Existent Leaf Position - (File: `merkle-tools/src/lib.rs`, `contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` accept a caller-supplied `tx_index` that is never validated against the actual tree depth. Because `compute_root_from_merkle_proof` uses only bit-by-bit modular arithmetic (`% 2`, `/= 2`), any index of the form `k * 2^depth + real_index` produces an identical Merkle root as `real_index`. An unprivileged NEAR caller can therefore claim a transaction sits at a non-existent leaf position while the proof still returns `true`.

---

### Finding Description

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` reconstructs the Merkle root by iterating over each proof element and branching left or right based on `current_position % 2`, then halving the position: [1](#0-0) 

After exactly `merkle_proof.len()` iterations the loop ends. Any bits of `transaction_position` above bit index `merkle_proof.len() - 1` are silently discarded by the repeated `/= 2`. Concretely, for a block whose Merkle tree has depth `d` (i.e., `merkle_proof.len() == d`):

```
compute_root_from_merkle_proof(tx_hash, real_index, proof)
  == compute_root_from_merkle_proof(tx_hash, k * 2^d + real_index, proof)
```

for any integer `k ≥ 1`.

`verify_transaction_inclusion` passes `args.tx_index` directly to this function without any bounds check: [2](#0-1) 

`tx_index` is a caller-controlled `u64` field in `ProofArgs`: [3](#0-2) 

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after the coinbase check, so it inherits the same flaw: [4](#0-3) 

---

### Impact Explanation

A consuming dApp that calls `verify_transaction_inclusion` and then uses the caller-supplied `tx_index` to make a security decision (e.g., "is this a coinbase transaction at index 0?", "is this the first output?") can be deceived. An attacker holds a valid Merkle proof for a real transaction at `real_index` (e.g., the coinbase at index `0`) and submits:

```
tx_index = 2^depth + 0   // behaves identically to index 0 inside the verifier
```

The contract returns `true`. The dApp observes `tx_index != 0` and incorrectly concludes the transaction is not a coinbase. The corrupted invariant is: **the verified `tx_index` does not correspond to any leaf that exists in the tree**, yet the proof is accepted as valid.

---

### Likelihood Explanation

The entry point is the public, permissionless `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` NEAR methods. No privileged role is required. Any caller who possesses a valid Merkle proof for a real transaction can trivially add `2^depth` to the real index and submit the modified `tx_index`. The depth is directly observable from `merkle_proof.len()`, which is also caller-supplied.

---

### Recommendation

Add a bounds check in `verify_transaction_inclusion` (and/or inside `compute_root_from_merkle_proof`) to ensure the supplied position is within the valid range for the given proof length:

```rust
require!(
    args.tx_index < (1u64 << args.merkle_proof.len()),
    "tx_index exceeds the tree depth implied by the merkle proof"
);
```

This mirrors the fix recommended in the reference report: validate the index against the tree height before using it in the proof computation.

---

### Proof of Concept

Suppose a block has 4 transactions (depth = 2). The coinbase is at `real_index = 0`. A valid Merkle proof `[sibling_0, parent_sibling]` exists.

**Legitimate call (index 0):**
- Level 0: `0 % 2 == 0` → hash left; position becomes `0`
- Level 1: `0 % 2 == 0` → hash left; position becomes `0`
- Returns the correct Merkle root. ✓

**Attacker call (index `4 = 2^2 + 0`):**
- Level 0: `4 % 2 == 0` → hash left; position becomes `2`
- Level 1: `2 % 2 == 0` → hash left; position becomes `1`
- Returns the **same** Merkle root. ✓

`verify_transaction_inclusion` returns `true` for `tx_index = 4`, even though leaf 4 does not exist in a 4-transaction tree. A dApp checking `tx_index == 0` to detect coinbase transactions would be bypassed. [5](#0-4) [6](#0-5)

### Citations

**File:** merkle-tools/src/lib.rs (L34-51)
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
