### Title
SPV Proof Permanently Rejected for Single-Transaction Blocks — (`File: contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` (and by extension `verify_transaction_inclusion_v2`, which calls it internally) unconditionally panics when the supplied `merkle_proof` is empty. For a Bitcoin block that contains exactly one transaction, the valid merkle proof **is** the empty list — the transaction hash equals the merkle root directly. The guard therefore permanently blocks verification of any such transaction, even though the proof is cryptographically correct.

### Finding Description

In `verify_transaction_inclusion`, after confirming the block is on the main chain and fetching the header, the function executes:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [1](#0-0) 

The `require!` fires before the computation is attempted. For a single-transaction block the empty-proof path through `compute_root_from_merkle_proof` is:

```rust
// merkle_proof is empty → loop body never executes → returns transaction_hash unchanged
let mut current_hash = transaction_hash;
for proof_hash in merkle_proof { … }   // skipped
current_hash   // == tx_id == merkle_root  ✓
``` [2](#0-1) 

So `compute_root_from_merkle_proof(tx_id, 0, &[])` would return `tx_id`, which equals `header.block_header.merkle_root` for a single-transaction block — a correct result. The guard prevents this correct result from ever being produced.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` via `self.verify_transaction_inclusion(args.into())` after its own coinbase-proof check passes, so it inherits the same lockout: [3](#0-2) 

The coinbase proof check in `verify_transaction_inclusion_v2` itself succeeds for a single-transaction block (both `merkle_proof` and `coinbase_merkle_proof` are empty, lengths match, and `compute_root_from_merkle_proof(coinbase_tx_id, 0, &[])` returns `coinbase_tx_id == merkle_root`), so execution reaches the inner call — which then panics unconditionally. [4](#0-3) 

### Impact Explanation

Any downstream bridge, payment channel, or cross-chain application that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` to release funds locked against a Bitcoin transaction will be permanently blocked if that transaction happens to be the sole transaction in its block. The contract returns no `false` — it panics, causing the NEAR transaction to revert. There is no alternative code path; the proof cannot be reformulated to be non-empty while remaining valid. Funds gated on that verification are permanently inaccessible.

**Impact: 4 / 5**

### Likelihood Explanation

Single-transaction blocks (containing only the coinbase) are uncommon in modern Bitcoin but are entirely valid and have occurred throughout Bitcoin's history, including during low-fee periods and early chain history. A relayer faithfully submitting such a block to the contract creates the lockout condition for any downstream consumer of the SPV API. No attacker action is required — the condition arises from normal chain data.

**Likelihood: 2 / 5**

### Recommendation

Remove the blanket empty-proof guard and instead handle the single-transaction case explicitly:

```rust
// Before computing the root, allow an empty proof only when tx_index == 0
// and the tx_id already equals the stored merkle_root.
if args.merkle_proof.is_empty() {
    return args.tx_index == 0 && args.tx_id == header.block_header.merkle_root;
}
```

This preserves the intent (reject malformed proofs) while correctly accepting the degenerate-but-valid single-transaction case.

### Proof of Concept

1. Deploy the contract (Bitcoin feature flag) with `skip_pow_verification = true`.
2. Submit a block whose `merkle_root` equals a single transaction hash `T` (i.e., the block contains only that one transaction).
3. Wait for the required number of confirmations.
4. Call `verify_transaction_inclusion_v2` with:
   - `tx_id = T`
   - `tx_block_blockhash` = the submitted block hash
   - `tx_index = 0`
   - `merkle_proof = []`
   - `coinbase_tx_id = T`
   - `coinbase_merkle_proof = []`
   - `confirmations = 1`
5. Observe the NEAR transaction reverts with `"Merkle proof is empty"` despite the proof being cryptographically valid (`T == merkle_root`).

The coinbase-proof check at line 359–364 passes (empty proof → `compute_root_from_merkle_proof` returns `T` = `merkle_root`). Execution reaches `verify_transaction_inclusion`, which panics at line 315 before the root comparison is ever evaluated. [5](#0-4)

### Citations

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

**File:** merkle-tools/src/lib.rs (L38-51)
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
```
