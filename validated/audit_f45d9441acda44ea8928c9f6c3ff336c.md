### Title
Caller-Supplied `confirmations: 0` Bypasses Confirmation Security in `verify_transaction_inclusion` — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` accept a caller-supplied `confirmations` field with no minimum enforcement. Any unprivileged NEAR caller may pass `confirmations: 0`, causing the confirmation-depth check to trivially pass for every mainchain block, including the chain tip with zero confirmations. This is the direct analog of `amountOutMinimum: 0` in the reference report: a protection parameter that exists in the interface but is never bounded from below, so the protection it is supposed to provide can be silently nullified by the caller.

---

### Finding Description

`verify_transaction_inclusion` performs two checks on the caller-supplied `confirmations` value:

**Check 1 — upper bound only:**
```rust
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [1](#0-0) 

This rejects values that are *too large*, but imposes no lower bound. `confirmations = 0` passes silently.

**Check 2 — depth comparison:**
```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [2](#0-1) 

When `confirmations = 0`, the right-hand side is `0`. Because `block_height` is `u64`, the left-hand side is always `≥ 0`. The require always passes, regardless of how recently the target block was submitted or how close it is to the chain tip.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after converting its args, so it inherits the same flaw: [3](#0-2) 

The `confirmations` field is part of the public `ProofArgs` and `ProofArgsV2` structs, freely set by any caller: [4](#0-3) 

---

### Impact Explanation

A downstream NEAR contract (bridge, payment processor, cross-chain application) that calls `verify_transaction_inclusion` with `confirmations: 0` receives `true` for any transaction whose block appears anywhere in the current mainchain — including the chain tip, which has received zero additional confirmations and is maximally exposed to reorganization. If the relayer submits a block and an attacker immediately triggers a proof check with `confirmations: 0`, the proof passes even though the block could be orphaned in the next reorg. The corrupted invariant is the contract's guarantee that a verified transaction has at least N blocks of cumulative PoW protecting it; with `confirmations: 0` that guarantee is reduced to zero.

---

### Likelihood Explanation

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are public, `#[pause]`-gated functions callable by any unprivileged NEAR account. No staking, role, or deposit is required beyond the standard NEAR gas. A consuming contract that omits or zeroes the confirmations field — whether by mistake or by an attacker who controls the proof-submission path — triggers the issue on every call. The entry path is direct and requires no privileged access.

---

### Recommendation

Enforce a minimum confirmations floor inside the contract before any other check:

```rust
require!(args.confirmations >= 1, "confirmations must be at least 1");
```

Ideally, document a recommended minimum (e.g., 6 for Bitcoin mainnet) and consider making the floor a contract-level configuration parameter set at `init` time, analogous to how `gc_threshold` is configured, so that operators can tune it per chain without redeployment.

---

### Proof of Concept

1. Deploy the contract (Bitcoin feature) with any valid genesis and a few submitted blocks so the mainchain tip is at height H.
2. Submit one additional block containing a target transaction at height H+1 (the new tip).
3. Call `verify_transaction_inclusion` with:
   - `tx_block_blockhash` = hash of the H+1 block
   - `merkle_proof` = valid single-element proof for the transaction
   - `confirmations = 0`
4. The depth check evaluates `(H+1 - (H+1)) + 1 = 1 >= 0` → passes.
5. The function returns `true` for a transaction with zero confirmations beyond its own block.
6. A downstream contract acting on this result treats the transaction as finalized, while the block at H+1 remains fully exposed to reorganization.

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

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
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
