### Title
`verify_transaction_inclusion` permanently reverts for single-transaction blocks due to hardcoded non-empty proof requirement - (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (and by extension `verify_transaction_inclusion_v2`) contains a hardcoded `require!(!args.merkle_proof.is_empty(), ...)` guard that makes it impossible to verify transaction inclusion for any block containing only a single transaction. For such blocks the mathematically correct proof is an empty vector, yet the function always panics when given one.

---

### Finding Description

In Bitcoin and its derivatives, a block whose merkle tree has exactly one leaf (e.g., a coinbase-only block) has `merkle_root == tx_hash`. No sibling hashes are needed; the correct `merkle_proof` is `[]`.

`verify_transaction_inclusion` unconditionally rejects that correct proof:

```rust
// contract/src/lib.rs  line 315
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [1](#0-0) 

This guard fires **before** `compute_root_from_merkle_proof` is ever called, so the function can never return `true` for a single-transaction block regardless of how correct the caller's inputs are.

`verify_transaction_inclusion_v2` delegates to the deprecated v1 after its own coinbase-proof check:

```rust
// contract/src/lib.rs  line 367-368
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [2](#0-1) 

For a single-transaction block both `merkle_proof` and `coinbase_merkle_proof` are empty. The length-equality guard passes, the coinbase-root check passes (empty proof returns `coinbase_tx_id`, which equals `merkle_root`), and then the call falls into v1 where it panics at the empty-proof guard.

The analog to the external report is exact:

| External report | This repository |
|---|---|
| `claimReward` requires `stake.initialized` which is never set → permanently broken | `verify_transaction_inclusion` requires `!merkle_proof.is_empty()` but the correct proof for a single-tx block IS empty → permanently broken for those blocks |

---

### Impact Explanation

Any cross-chain application (bridge, oracle, SPV client) that relies on this contract to confirm Bitcoin transactions cannot process transactions from single-transaction blocks. A user whose on-chain BTC transaction landed in such a block cannot prove inclusion; funds or cross-chain operations depending on that proof are permanently stuck.

---

### Likelihood Explanation

Single-transaction (coinbase-only) blocks occur regularly on Bitcoin mainnet during periods of low mempool activity and are a normal part of the protocol. The condition is not exotic or attacker-controlled; it arises from ordinary network operation.

---

### Recommendation

Remove the blanket empty-proof guard and handle the degenerate case explicitly:

```rust
if args.merkle_proof.is_empty() {
    return args.tx_id == header.block_header.merkle_root;
}
```

This correctly handles single-transaction blocks while preserving the existing logic for all multi-transaction blocks.

---

### Proof of Concept

1. A Bitcoin block at height H contains only the coinbase transaction with hash `T`; its `merkle_root = T`.
2. The block is accepted into the contract's main chain via `submit_blocks`.
3. A caller invokes `verify_transaction_inclusion` with:
   - `tx_id = T`
   - `tx_block_blockhash = <block hash at H>`
   - `tx_index = 0`
   - `merkle_proof = []` ← mathematically correct
   - `confirmations = 1`
4. All guards before line 315 pass (block is in main chain, confirmations satisfied).
5. Execution reaches `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` and panics.
6. The caller can never obtain a `true` result for this transaction; no alternative proof exists because the merkle tree has only one node. [3](#0-2) [4](#0-3) [5](#0-4)

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
