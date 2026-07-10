### Title
Empty Merkle Proof Causes Unconditional Panic for Single-Transaction Block Verification — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (and by inheritance `verify_transaction_inclusion_v2`) unconditionally panics when `merkle_proof` is empty. However, an empty Merkle proof is the **correct and only valid proof** for a transaction in a single-transaction Bitcoin block, where the Merkle root equals the transaction hash directly. The guard is overly restrictive and blocks a legitimate, reachable verification path.

---

### Finding Description

In `contract/src/lib.rs`, `verify_transaction_inclusion` contains a hard guard:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [1](#0-0) 

This fires before the Merkle root computation is attempted. The function then delegates to `merkle_tools::compute_root_from_merkle_proof`: [2](#0-1) 

In `merkle-tools/src/lib.rs`, `compute_root_from_merkle_proof` iterates over the proof slice:

```rust
for proof_hash in merkle_proof {
    // combine hashes
}
current_hash  // returned unchanged if proof is empty
``` [3](#0-2) 

When `merkle_proof` is empty, the loop body never executes and `current_hash` is returned as-is — i.e., the function correctly returns `transaction_hash` unchanged. This is the **mathematically correct result** for a single-transaction block: the Merkle root of a tree with one leaf is that leaf itself, so no sibling hashes are needed to reconstruct the root.

The guard at line 315 fires **before** this correct computation can occur, causing an unconditional panic for any caller supplying an empty proof.

`verify_transaction_inclusion_v2` inherits the same defect. For a single-transaction block, both `merkle_proof` and `coinbase_merkle_proof` are empty (equal length, so the length check at line 349 passes), the coinbase root check at lines 358–365 passes (since `coinbase_tx_id == merkle_root` for a single-tx block), and then the delegated call to `verify_transaction_inclusion` panics at line 315. [4](#0-3) 

---

### Impact Explanation

Any external NEAR DApp calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` to confirm a transaction in a single-transaction Bitcoin block will receive a panic instead of a `true` result. Because these functions are the contract's sole SPV verification interface, downstream contracts that gate fund releases, cross-chain bridges, or other protocol actions on a `true` return value will be permanently blocked for this class of valid Bitcoin transactions. The verification result is corrupted from `true` to a hard revert, not merely delayed.

---

### Likelihood Explanation

Single-transaction blocks occur on Bitcoin mainnet (blocks containing only the coinbase transaction) and are common on testnets and during low-activity periods. Any relayer or user who submits such a block header and then attempts to prove the coinbase transaction's inclusion will trigger this path. The entry point is fully unprivileged — `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are public, `#[pause]`-gated (not role-gated) methods callable by any NEAR account. [5](#0-4) [6](#0-5) 

---

### Recommendation

Remove the `require!(!args.merkle_proof.is_empty(), ...)` guard from `verify_transaction_inclusion`. The downstream `compute_root_from_merkle_proof` already handles the empty-proof case correctly and safely — when the proof is empty it returns `transaction_hash` unchanged, which will equal `merkle_root` if and only if the block contains exactly one transaction. No additional guard is needed; the root-equality check at the end of the function is sufficient.

```diff
-    require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
-
     merkle_tools::compute_root_from_merkle_proof(
         args.tx_id,
         usize::try_from(args.tx_index).unwrap(),
         &args.merkle_proof,
     ) == header.block_header.merkle_root
```

---

### Proof of Concept

1. Initialize the contract with a real Bitcoin block header whose Merkle root equals a single transaction hash (a coinbase-only block).
2. Call `verify_transaction_inclusion` with:
   - `tx_id` = the coinbase transaction hash (= the block's Merkle root)
   - `tx_block_blockhash` = the block hash
   - `tx_index` = 0
   - `merkle_proof` = `[]` (empty — correct for a single-tx block)
   - `confirmations` = 1
3. **Expected result:** `true` (since `compute_root_from_merkle_proof(tx_id, 0, &[])` returns `tx_id` == `merkle_root`).
4. **Actual result:** NEAR runtime panic — `"Merkle proof is empty"` — triggered at line 315 before the computation is reached. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L287-288)
```rust
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

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
