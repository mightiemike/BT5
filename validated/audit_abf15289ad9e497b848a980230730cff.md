### Title
Zero-Confirmation Proof Acceptance via Missing Non-Zero Validation on `confirmations` — (`contract/src/lib.rs`)

### Summary
Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` accept a caller-supplied `confirmations: u64` with no lower-bound validation. Supplying `confirmations = 0` trivially satisfies every guard in the function, causing it to return `true` for a transaction in the chain-tip block that has zero actual confirmations and is fully reorganizable.

### Finding Description

`ProofArgs` and `ProofArgsV2` both carry a `confirmations: u64` field with no constraints at the type or struct level. [1](#0-0) [2](#0-1) 

Inside `verify_transaction_inclusion` there are exactly two guards that are supposed to enforce the confirmation requirement:

**Guard 1** — upper-bound check:
```rust
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
```
When `confirmations = 0`, `0 <= gc_threshold` is always true. Guard passes unconditionally. [3](#0-2) 

**Guard 2** — depth check:
```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
```
For `u64`, `anything >= 0` is always true. Guard passes unconditionally. [4](#0-3) 

After both guards pass, the function proceeds to Merkle-root verification and returns `true` for any transaction in any main-chain block — including the tip block with zero actual confirmations.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its own coinbase-proof check, so it inherits the same flaw identically. [5](#0-4) 

### Impact Explanation

A downstream NEAR contract consuming the `true` result receives a "verified" proof for a transaction that sits in the chain tip and is fully reorganizable. If that contract gates an irreversible action (e.g., releasing bridged assets, minting tokens) on this result, an attacker who controls a transaction in the tip block can trigger the action before the block is buried, then benefit from a chain reorganization that removes the transaction from the canonical chain. The corrupted protocol value is the proof result itself: `verify_transaction_inclusion → true` for a 0-confirmation transaction.

### Likelihood Explanation

Both functions are public (`#[pause]` only, no role restriction) and callable by any unprivileged NEAR account. `confirmations = 0` is a valid `u64` that deserializes without error via Borsh. No special knowledge, key material, or privileged role is required. Any downstream contract that forwards a user-controlled `confirmations` value, or that omits its own lower-bound check before calling the light client, is directly exploitable.

### Recommendation

Add a non-zero guard at the top of `verify_transaction_inclusion`, before any other logic:

```rust
require!(args.confirmations >= 1, "confirmations must be at least 1");
```

This mirrors the fix applied in the referenced report: enforce a minimum valid value on the critical numeric parameter before any downstream logic executes.

### Proof of Concept

1. Deploy the BTC light client with a valid genesis and at least one submitted block.
2. Obtain a valid `(tx_id, tx_block_blockhash, tx_index, merkle_proof)` tuple for a transaction in the current chain-tip block (0 actual confirmations).
3. Call `verify_transaction_inclusion` with `confirmations = 0`.
4. Both guards pass; the Merkle proof is verified; the function returns `true`.
5. The tip block is subsequently reorganized away.
6. The downstream contract has already acted on the `true` result for a transaction that no longer exists on the canonical chain.

### Citations

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

**File:** btc-types/src/contract_args.rs (L26-36)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
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

**File:** contract/src/lib.rs (L289-292)
```rust
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );
```

**File:** contract/src/lib.rs (L304-308)
```rust
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );
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
