### Title
Zero-Confirmation Bypass in SPV Proof Verification Allows Confirmation Requirement to Be Skipped — (`File: contract/src/lib.rs`)

### Summary

`BtcLightClient::verify_transaction_inclusion` (and `verify_transaction_inclusion_v2`) accepts a caller-supplied `confirmations: u64` parameter. When the caller passes `confirmations = 0`, the confirmation-depth guard is trivially satisfied (`u64 >= 0` is always true), bypassing the confirmation requirement entirely. Any unprivileged NEAR caller or recipient contract can obtain a `true` SPV proof result for a transaction with zero actual on-chain confirmations.

### Finding Description

The confirmation check in `verify_transaction_inclusion` is:

```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [1](#0-0) 

When `args.confirmations = 0`, the expression evaluates to `(any_u64) >= 0`, which is unconditionally `true` for `u64`. The preceding guard:

```rust
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [2](#0-1) 

also passes trivially because `0 <= gc_threshold` is always true. There is no lower-bound check on `args.confirmations` — no `require!(args.confirmations >= 1, ...)` or equivalent guard exists anywhere in the function.

`confirmations` is a plain `u64` field in `ProofArgs` with no validation at the type or deserialization layer:

```rust
pub struct ProofArgs {
    pub confirmations: u64,
    ...
}
``` [3](#0-2) 

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after passing `args.confirmations` through unchanged via `From<ProofArgsV2>`: [4](#0-3) 

So the bypass applies to both endpoints.

**Structural analog to the reported bug:** In `VariableSupplyERC20Token::mint`, `mintableSupply = 0` caused the guard `if(mintableSupply > 0)` to be skipped, allowing unlimited minting. Here, `confirmations = 0` causes the guard `X >= args.confirmations` to be trivially satisfied, allowing unlimited proof acceptance with zero confirmation depth. In both cases, the sentinel value `0` disables the enforcement of a critical limit.

### Impact Explanation

A recipient contract that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` and forwards a user-supplied `confirmations` value (or one that can be set to `0`) will receive `true` for a transaction that has zero actual Bitcoin confirmations. This corrupts the proof result: the contract's invariant that "a transaction must have at least N confirmations to be considered final" is broken. Downstream actions gated on this result — releasing NEAR tokens, minting wrapped assets, executing cross-chain swaps — can be triggered against a 0-conf Bitcoin transaction that is still trivially double-spendable.

### Likelihood Explanation

Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are public, unguarded (beyond `#[pause]`) NEAR contract methods callable by any account: [5](#0-4) [6](#0-5) 

No privileged role, staking, or special permission is required. Any NEAR account can submit a `ProofArgs` with `confirmations = 0`. The only prerequisite is that the target transaction's block has already been submitted to the light client by a relayer — a normal operational condition.

### Recommendation

Add an explicit lower-bound check at the start of `verify_transaction_inclusion` to reject `confirmations = 0`:

```rust
require!(args.confirmations >= 1, "CONFIRMATIONS_MUST_BE_AT_LEAST_ONE");
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
```

This mirrors the fix applied to the reported `VariableSupplyERC20Token` bug: instead of relying on `X >= 0` being trivially true, enforce that the caller-supplied limit is strictly positive before evaluating the depth check.

### Proof of Concept

1. Relayer submits block `B` at height `H` containing transaction `TX` (normal operation).
2. Attacker immediately calls `verify_transaction_inclusion` with:
   - `tx_id` = hash of `TX`
   - `tx_block_blockhash` = hash of `B`
   - `tx_index` = correct index
   - `merkle_proof` = valid Merkle path for `TX`
   - `confirmations = 0`
3. Guard check: `0 <= gc_threshold` → passes.
4. Depth check: `(tip_height - H + 1) >= 0` → always `true` for `u64`.
5. Merkle proof is valid → function returns `true`.
6. Downstream contract releases funds / mints wrapped tokens.
7. Attacker double-spends `TX` on Bitcoin (0-conf replacement), invalidating the original transaction while keeping the NEAR-side payout.

### Citations

**File:** contract/src/lib.rs (L287-288)
```rust
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L289-292)
```rust
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );
```

**File:** contract/src/lib.rs (L303-308)
```rust
        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );
```

**File:** contract/src/lib.rs (L346-347)
```rust
    #[pause]
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
```

**File:** btc-types/src/contract_args.rs (L16-24)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```

**File:** btc-types/src/contract_args.rs (L38-47)
```rust
impl From<ProofArgsV2> for ProofArgs {
    fn from(args: ProofArgsV2) -> Self {
        Self {
            tx_id: args.tx_id,
            tx_block_blockhash: args.tx_block_blockhash,
            tx_index: args.tx_index,
            merkle_proof: args.merkle_proof,
            confirmations: args.confirmations,
        }
    }
```
