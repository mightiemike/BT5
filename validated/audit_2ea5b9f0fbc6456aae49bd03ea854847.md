### Title
Zero Confirmations Bypass in Transaction Inclusion Verification - (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` check that `args.confirmations` does not exceed `gc_threshold` (upper bound), but never check that `args.confirmations` is greater than zero. An unprivileged NEAR caller can supply `confirmations = 0`, which trivially satisfies both the upper-bound guard and the depth comparison, causing the function to return `true` for a transaction in any main-chain block with zero confirmation depth enforced.

---

### Finding Description

In `contract/src/lib.rs`, `verify_transaction_inclusion` performs two guards on the caller-supplied `confirmations` value:

**Guard 1 — upper bound only:**
```rust
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [1](#0-0) 

**Guard 2 — depth check:**
```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [2](#0-1) 

There is no lower-bound check (`confirmations > 0`). When `confirmations = 0`:

- Guard 1: `0 <= gc_threshold` — always passes.
- Guard 2: `(any_u64_value) >= 0` — always true for `u64` arithmetic; the `saturating_sub(...) + 1` expression is always ≥ 0.

The function then proceeds directly to Merkle proof verification and returns `true` if the proof is valid, regardless of how recently the block was added to the chain.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its own coinbase-proof check, so it inherits the same flaw: [3](#0-2) 

The `confirmations` field is part of `ProofArgs` / `ProofArgsV2`, which are fully caller-controlled: [4](#0-3) 

---

### Impact Explanation

Any recipient smart contract that calls `verify_transaction_inclusion_v2` (or the deprecated v1) and relies on the returned `bool` to gate a privileged action (e.g., minting wrapped BTC, releasing funds) can be bypassed by supplying `confirmations = 0`. The function will return `true` for a transaction in the most recently added main-chain block — a block that could still be reorganized away. The confirmation parameter exists precisely to provide reorg safety; setting it to zero eliminates that safety entirely while the contract reports success. The corrupted result is the proof verification return value itself: `true` is returned when the caller's security policy requires at least N confirmations but zero were enforced.

---

### Likelihood Explanation

The entry point is a public, unprivileged NEAR contract call. No staking, role, or key is required. The attacker only needs to construct a `ProofArgsV2` (or `ProofArgs`) with `confirmations: 0` and a valid Merkle proof for a transaction already in the main chain. This is straightforward for any relayer or application user who has observed the chain state.

---

### Recommendation

Add an explicit lower-bound check at the top of `verify_transaction_inclusion`:

```rust
require!(args.confirmations > 0, "Confirmations must be at least 1");
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
```

This mirrors the fix recommended in the reference report: validate both the lower and upper bounds of the caller-supplied numeric parameter before using it in a security-critical comparison.

---

### Proof of Concept

1. Deploy the contract (Bitcoin feature) with any valid genesis set and `gc_threshold = 100`.
2. Submit one block header so the chain has height 1.
3. Call `verify_transaction_inclusion_v2` with:
   - `tx_block_blockhash` = hash of the block at height 1 (tip, zero blocks above it)
   - `confirmations = 0`
   - A valid Merkle proof for any transaction in that block
4. **Guard 1**: `0 <= 100` → passes.
5. **Guard 2**: `(1 - 1) + 1 = 1 >= 0` → passes.
6. The function returns `true`.
7. A recipient contract acting on this result treats the transaction as confirmed with the caller's chosen security threshold, which was silently set to zero.

### Citations

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

**File:** contract/src/lib.rs (L367-369)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** btc-types/src/contract_args.rs (L16-36)
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
