### Title
`verify_transaction_inclusion` Incorrectly Rejects Valid Proofs for Single-Transaction Blocks - (`File: contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` contains a guard `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` that unconditionally panics when the merkle proof is empty. However, when a Bitcoin block contains exactly one transaction (e.g., a coinbase-only block), the merkle proof is legitimately empty — the merkle root *is* the transaction hash. The guard does not account for this valid edge case, causing the function to always panic instead of returning `true` for a provably valid inclusion.

### Finding Description

In `verify_transaction_inclusion`, after confirming the block is on the main chain and has enough confirmations, the code checks:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [1](#0-0) 

This guard fires unconditionally on any empty proof. However, `compute_root_from_merkle_proof` is defined to handle an empty proof correctly — it simply returns the `transaction_hash` unchanged:

```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    ...
    for proof_hash in merkle_proof { ... }  // loop body never executes
    current_hash  // returns transaction_hash as-is
}
``` [2](#0-1) 

For a block with exactly one transaction, the Bitcoin Merkle tree has a single leaf, so `merkle_root == tx_id` and the proof path is empty. The comparison `compute_root_from_merkle_proof(tx_id, 0, &[]) == header.block_header.merkle_root` would evaluate to `true` — but the `require!` guard prevents this path from ever being reached.

The same defect propagates into `verify_transaction_inclusion_v2`, which calls `verify_transaction_inclusion` internally after its own coinbase-proof check: [3](#0-2) 

For a single-transaction block, `coinbase_merkle_proof` is also empty, so the length-equality check passes, the coinbase proof check passes (since `compute_root_from_merkle_proof(coinbase_tx_id, 0, &[]) == merkle_root` when `coinbase_tx_id == merkle_root`), and then the inner call panics on the empty-proof guard.

### Impact Explanation

Any NEAR caller or recipient contract that invokes `verify_transaction_inclusion` (or `verify_transaction_inclusion_v2`) to verify a transaction in a single-transaction block will always receive a panic instead of `true`. Downstream consumers — bridges, atomic swaps, cross-chain lending protocols — that rely on this function to release funds or authorize cross-chain operations will be permanently blocked for this class of valid Bitcoin blocks. The function produces an incorrect result (panic/false negative) for a provably valid input, breaking the core SPV guarantee the contract is designed to provide.

### Likelihood Explanation

Bitcoin blocks containing only the coinbase transaction are not rare. They occur during periods of low mempool activity, at the start of a new difficulty epoch, or in early Bitcoin history. Any relayer or user who needs to prove inclusion of a coinbase transaction (e.g., to prove a miner reward was paid, or to verify a block reward in a cross-chain protocol) will trigger this bug. The entry path requires no special privileges — `verify_transaction_inclusion` is a public, unpaused-by-default function callable by any NEAR account. [4](#0-3) 

### Recommendation

Remove the blanket `require!(!args.merkle_proof.is_empty(), ...)` guard. Instead, only reject an empty proof when the transaction hash does not equal the block's merkle root (i.e., when the block has more than one transaction). The corrected logic should be:

```rust
// Only require a non-empty proof if the tx_id is not itself the merkle root
// (i.e., the block has more than one transaction)
if args.tx_id != header.block_header.merkle_root {
    require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
}
```

Or equivalently, simply remove the guard entirely and let `compute_root_from_merkle_proof` handle the empty-proof case naturally, since it already returns the transaction hash unchanged when the proof is empty.

### Proof of Concept

1. Submit a Bitcoin block header for a block that contains exactly one transaction (e.g., a coinbase-only block). The block's `merkle_root` equals the coinbase `tx_id`.
2. Wait for the required number of confirmations.
3. Call `verify_transaction_inclusion` with:
   - `tx_id` = the coinbase transaction hash (= `merkle_root`)
   - `tx_block_blockhash` = the block hash
   - `tx_index` = 0
   - `merkle_proof` = `[]` (empty, as is mathematically correct)
   - `confirmations` = any valid value
4. **Expected**: `true` (the transaction is provably included; `compute_root_from_merkle_proof(tx_id, 0, &[]) == merkle_root`)
5. **Actual**: The contract panics with `"Merkle proof is empty"` at line 315, before the comparison is ever evaluated. [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L287-323)
```rust
    #[pause]
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
