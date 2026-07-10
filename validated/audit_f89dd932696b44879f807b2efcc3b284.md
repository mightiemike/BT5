### Title
Empty Merkle Proof Unconditionally Rejected for Single-Transaction Blocks, Permanently Breaking Verification - (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` contains a guard that unconditionally panics when `merkle_proof` is empty. For a Bitcoin block containing exactly one transaction, an empty proof is mathematically correct — the merkle root equals the transaction hash directly, requiring no sibling hashes. The guard fires before the proof computation, making it permanently impossible for any unprivileged NEAR caller to verify a transaction in a single-transaction block through either the v1 or v2 API.

---

### Finding Description

In `contract/src/lib.rs` at line 315, the following guard is present inside `verify_transaction_inclusion`:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [1](#0-0) 

This check fires unconditionally before the proof computation at lines 318–322:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [2](#0-1) 

However, `compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` correctly handles an empty proof: when `merkle_proof` is empty, the `for` loop does not execute and `current_hash` (the transaction hash itself) is returned:

```rust
for proof_hash in merkle_proof {
    ...
}
current_hash  // returns tx_id unchanged when proof is empty
``` [3](#0-2) 

For a block with exactly one transaction, the Bitcoin protocol defines `merkle_root = txid`. An empty proof is the only correct proof for this case. The guard at line 315 rejects it unconditionally, causing the call to always panic with `"Merkle proof is empty"`.

The bug also propagates into `verify_transaction_inclusion_v2`, which delegates to `verify_transaction_inclusion` via `self.verify_transaction_inclusion(args.into())` at line 368: [4](#0-3) 

For a single-transaction block, `verify_transaction_inclusion_v2` passes its own earlier checks (both proof lengths are 0, and `compute_root_from_merkle_proof(coinbase_tx_id, 0, &[])` correctly returns `coinbase_tx_id == merkle_root`), but then panics inside the inner call. [5](#0-4) 

---

### Impact Explanation

Any downstream contract or user that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` to verify a transaction in a single-transaction Bitcoin block will always receive a panic. The verification result can never be `true` or `false` — it is permanently unreachable. Consumers that depend on this API for cross-chain settlement, bridge unlocks, or SPV proof gating are permanently blocked for this class of valid Bitcoin blocks. Single-transaction blocks are real and common (e.g., early Bitcoin blocks, and any block where a miner includes only the coinbase transaction).

---

### Likelihood Explanation

The entry path requires no privilege: any NEAR account can call `verify_transaction_inclusion_v2` with a borsh-encoded `ProofArgsV2` where both `merkle_proof` and `coinbase_merkle_proof` are empty vectors and `tx_block_blockhash` points to a single-transaction block already stored in the contract. Single-transaction blocks exist on Bitcoin mainnet (e.g., block 0, block 1, and many early blocks). The trigger is deterministic and requires no adversarial chain data — a legitimate user submitting a valid proof for a real block hits the panic every time.

---

### Recommendation

Remove the blanket `require!(!args.merkle_proof.is_empty(), ...)` guard. The proof computation itself is the correct validator: if the proof is empty and `tx_id == merkle_root`, the function already returns `true` correctly. If the proof is empty and `tx_id != merkle_root`, it returns `false`. No separate emptiness check is needed.

```rust
// Remove this line:
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [1](#0-0) 

---

### Proof of Concept

1. Submit a Bitcoin block header whose `merkle_root` equals a single transaction hash `T` (a coinbase-only block). This is valid and accepted by `submit_blocks`.
2. Call `verify_transaction_inclusion_v2` with:
   - `tx_id = T`
   - `tx_block_blockhash` = hash of the submitted block
   - `tx_index = 0`
   - `merkle_proof = []` (correct empty proof)
   - `coinbase_tx_id = T`
   - `coinbase_merkle_proof = []` (correct empty proof)
   - `confirmations = 1`
3. The length-equality check passes (both 0). The coinbase proof check passes (`compute_root_from_merkle_proof(T, 0, &[]) == T == merkle_root`). The inner `verify_transaction_inclusion` call panics: `"Merkle proof is empty"`.
4. The call reverts. The proof is permanently unverifiable despite being mathematically valid. [6](#0-5)

### Citations

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
