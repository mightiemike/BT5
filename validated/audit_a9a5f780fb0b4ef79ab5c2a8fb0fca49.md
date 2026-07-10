### Title
Unconditional Empty-Proof Guard Blocks Valid SPV Verification for Single-Transaction Blocks — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (and by delegation, `verify_transaction_inclusion_v2`) unconditionally rejects any call where `merkle_proof` is empty. However, an empty proof is mathematically correct when the block contains exactly one transaction: the merkle root equals the transaction hash, so no sibling nodes are needed. The guard fires before the computation and panics, permanently blocking any NEAR caller from verifying a valid transaction in a single-transaction block.

---

### Finding Description

In `verify_transaction_inclusion`, line 315 unconditionally asserts:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

This fires regardless of whether the proof is actually needed. The downstream computation in `compute_root_from_merkle_proof` handles an empty slice correctly — it simply returns `transaction_hash` unchanged:

```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    let mut current_position = transaction_position;

    for proof_hash in merkle_proof {   // loop body never executes when empty
        ...
    }
    current_hash   // returns transaction_hash as-is
}
```

For a block with exactly one transaction, `merkle_root == tx_id`, so `compute_root_from_merkle_proof(tx_id, 0, &[]) == tx_id == merkle_root` is a valid, passing proof. The `require!` guard at line 315 intercepts and panics before this correct result can be produced.

The same defect is reachable through `verify_transaction_inclusion_v2`, which passes its own coinbase-proof check (also with an empty slice, which succeeds for a single-tx block) and then delegates to `verify_transaction_inclusion` via `self.verify_transaction_inclusion(args.into())` at line 368, where the unconditional guard triggers.

---

### Impact Explanation

Any NEAR caller invoking either `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` for a transaction in a single-transaction block receives a contract panic and a `false`-equivalent result. The contract's core SPV guarantee — that it can verify any Bitcoin transaction whose block is stored — is broken for this class of blocks. Early Bitcoin blocks (heights 1 through many thousands) frequently contained only the coinbase transaction. Downstream contracts consuming the verification result will incorrectly treat valid inclusions as unverifiable.

---

### Likelihood Explanation

The entry path requires no privilege: both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are public, unpermissioned NEAR calls. Any relayer, dApp, or user submitting a proof for a single-transaction block (a common real-world scenario for early Bitcoin or low-activity periods) will trigger the panic deterministically. No adversarial input is required — a correct, honest proof is sufficient to reproduce the failure.

---

### Recommendation

Remove the unconditional guard at line 315 and instead allow `compute_root_from_merkle_proof` to run. The empty-proof case is already handled correctly by the computation. If a guard is desired for defense-in-depth, make it conditional on the block having more than one transaction, or simply rely on the root-comparison at lines 318–322 to reject invalid inputs:

```rust
// Remove this line:
// require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

// The existing computation already handles the empty-proof case correctly:
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
```

---

### Proof of Concept

1. Submit a Bitcoin block containing exactly one transaction (the coinbase). Its `merkle_root` equals `coinbase_tx_id`.
2. Call `verify_transaction_inclusion_v2` with:
   - `tx_id = coinbase_tx_id`
   - `tx_index = 0`
   - `merkle_proof = []` (empty — correct for a single-tx block)
   - `coinbase_tx_id = coinbase_tx_id`
   - `coinbase_merkle_proof = []` (empty — same length as `merkle_proof` ✓)
3. Inside `verify_transaction_inclusion_v2`:
   - Length check: `0 == 0` ✓ [1](#0-0) 
   - Coinbase proof: `compute_root_from_merkle_proof(coinbase_tx_id, 0, &[])` returns `coinbase_tx_id == merkle_root` ✓ [2](#0-1) 
   - Delegates to `verify_transaction_inclusion` [3](#0-2) 
4. Inside `verify_transaction_inclusion`, line 315 fires: `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` → **PANIC** [4](#0-3) 
5. The valid proof is rejected. The computation at lines 318–322, which would have returned `true`, is never reached. [5](#0-4) 

The `compute_root_from_merkle_proof` function confirms the empty-proof path is mathematically sound — the `for` loop simply does not execute and `transaction_hash` is returned unchanged. [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L315-315)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
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

**File:** contract/src/lib.rs (L348-351)
```rust
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );
```

**File:** contract/src/lib.rs (L358-365)
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

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```

**File:** merkle-tools/src/lib.rs (L38-52)
```rust
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
