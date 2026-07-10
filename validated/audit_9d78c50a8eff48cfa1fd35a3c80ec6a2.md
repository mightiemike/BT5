### Title
Legitimate SPV Proof Rejected for Single-Transaction Blocks Due to Spurious Empty-Proof Guard — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` contains a hard guard `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` that unconditionally panics before the merkle root computation is attempted. For a block containing exactly one transaction, the merkle proof is legitimately empty — the transaction hash *is* the merkle root — so the proof is mathematically valid but permanently rejected. Because `verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` internally, both public entry points are affected.

---

### Finding Description

In `verify_transaction_inclusion`, the empty-proof guard fires at line 315 before the actual root computation:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [1](#0-0) 

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` iterates over `merkle_proof`. When the slice is empty the loop body never executes and the function returns `transaction_hash` unchanged:

```rust
for proof_hash in merkle_proof {   // never entered when merkle_proof is empty
    ...
}
current_hash   // == transaction_hash
``` [2](#0-1) 

For a block with exactly one transaction, Bitcoin's merkle tree has depth 0: `merkle_root == tx_hash`. The correct proof is the empty vector. `compute_root_from_merkle_proof(tx_id, 0, &[])` would return `tx_id`, which equals `header.block_header.merkle_root`, so the proof is valid. The guard at line 315 panics before this comparison is ever reached.

`verify_transaction_inclusion_v2` — the current non-deprecated entry point — calls `verify_transaction_inclusion` at line 368 after its own coinbase-proof check passes:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [3](#0-2) 

For a single-transaction block, `coinbase_merkle_proof` is also empty, so the length-equality check at line 349 passes (`0 == 0`), the coinbase proof check at lines 358–365 passes (empty proof correctly returns `coinbase_tx_id == merkle_root`), and then the call to `verify_transaction_inclusion` panics on the empty-proof guard. Both public API functions are broken for this class of block. [4](#0-3) 

---

### Impact Explanation

Any downstream NEAR contract or off-chain consumer that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` to confirm a transaction in a single-transaction block receives a hard panic (not a `false` return — a full transaction revert). The transaction inclusion proof cannot be verified on-chain for such blocks, permanently blocking any bridge, payment channel, or SPV application that needs to prove coinbase-only blocks. The state of the light client itself is not corrupted, but the verification oracle is rendered useless for a well-defined class of valid Bitcoin blocks.

---

### Likelihood Explanation

Single-transaction blocks (containing only the coinbase transaction) are a normal part of Bitcoin's history and still occur today when miners produce empty blocks. They are not exotic edge cases. Any relayer that submits such a block to the contract and any user who subsequently tries to prove the coinbase transaction's inclusion will be permanently blocked. The trigger requires no privilege: any unprivileged NEAR caller can invoke `verify_transaction_inclusion_v2` with a valid proof for a single-transaction block and observe the panic.

---

### Recommendation

Remove the `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` guard entirely. The empty-proof case is already handled correctly by `compute_root_from_merkle_proof` — it returns the transaction hash itself, which is then compared against the block's merkle root. No separate guard is needed. If a guard is desired for other reasons, it must account for the single-transaction case:

```rust
// Only require a non-empty proof when the block has more than one transaction
// (i.e., when merkle_root != tx_id). The empty-proof case is valid and handled
// correctly by compute_root_from_merkle_proof.
```

---

### Proof of Concept

1. A Bitcoin miner produces a block containing only the coinbase transaction. The block's `merkle_root` equals the coinbase `tx_id`.
2. The relayer submits this block header via `submit_blocks`; it is accepted and stored in `headers_pool`.
3. A downstream contract calls `verify_transaction_inclusion_v2` with:
   - `tx_id = coinbase_tx_id`
   - `tx_index = 0`
   - `merkle_proof = []` (empty — correct for a single-tx block)
   - `coinbase_tx_id = coinbase_tx_id`
   - `coinbase_merkle_proof = []` (empty — same length, passes length check)
4. The coinbase proof check at lines 358–365 passes: `compute_root_from_merkle_proof(coinbase_tx_id, 0, &[])` returns `coinbase_tx_id == merkle_root`. ✓
5. `verify_transaction_inclusion` is called. Line 315 fires: `require!(!args.merkle_proof.is_empty())` → **panic: "Merkle proof is empty"**.
6. The transaction revert propagates to the caller. The valid proof is permanently unverifiable. [5](#0-4)

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

**File:** merkle-tools/src/lib.rs (L42-51)
```rust
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
