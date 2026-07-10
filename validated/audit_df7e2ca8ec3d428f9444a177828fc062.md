### Title
Unconstrained `confirmations` Parameter in `verify_transaction_inclusion` Allows Zero-Confirmation Proof Acceptance — (`File: contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` accept a caller-supplied `confirmations` value with no enforced minimum. Any unprivileged NEAR caller can pass `confirmations = 0`, causing the contract to return `true` for a transaction that has zero on-chain finality. Downstream contracts that gate fund releases on this result can be drained via a Bitcoin double-spend or shallow reorg.

### Finding Description

`verify_transaction_inclusion` enforces only an upper bound on `confirmations`: [1](#0-0) 

No lower bound is checked. The subsequent confirmation-depth guard is: [2](#0-1) 

When `args.confirmations == 0`, the condition becomes `(heaviest_block_height - target_block_height + 1) >= 0`. Because both operands are `u64` and the subtraction is saturating, this is always `true`. The function returns `true` for any transaction whose block appears anywhere on the current main chain, regardless of depth.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its coinbase-proof check, so it inherits the same flaw: [3](#0-2) 

### Impact Explanation

Any NEAR contract that calls `verify_transaction_inclusion{_v2}` to gate an action (e.g., releasing wrapped BTC, minting tokens, unlocking collateral) can be exploited. An attacker submits a Bitcoin transaction, waits for it to appear in a single block, immediately calls the light-client with `confirmations = 0`, receives `true`, triggers the downstream payout, and then either double-spends the Bitcoin or allows a shallow reorg to erase the original transaction. The light-client contract itself is the necessary vulnerable step: it is the authoritative source of truth for downstream contracts and it provides no floor on the security parameter.

### Likelihood Explanation

The entry point is fully unprivileged — `verify_transaction_inclusion` carries no access-control annotation and is callable by any NEAR account or contract. Bitcoin shallow reorgs (1–2 blocks) occur naturally several times per year; a deliberate double-spend against a low-hashrate window is a known attack. The attacker needs only to control the `confirmations` argument, which is entirely caller-supplied.

### Recommendation

Enforce a protocol-level minimum confirmation depth inside the contract. Reject any call where `args.confirmations` is below a safe floor (e.g., 6 for Bitcoin mainnet):

```rust
const MIN_CONFIRMATIONS: u64 = 6;
require!(
    args.confirmations >= MIN_CONFIRMATIONS,
    format!("Confirmations must be at least {MIN_CONFIRMATIONS}")
);
```

Alternatively, expose the minimum as a configurable contract parameter set at initialization, so it can be tuned per chain (Bitcoin vs. Litecoin vs. Dogecoin) without a code upgrade.

### Proof of Concept

1. Attacker sends 1 BTC to address `A` on Bitcoin mainnet.
2. The transaction is mined into block `B` at height `H`. The relayer submits `B` to the light client; `B` becomes the main-chain tip.
3. Attacker calls `verify_transaction_inclusion` on NEAR with:
   - `tx_id` = attacker's transaction hash
   - `tx_block_blockhash` = hash of block `B`
   - `tx_index` = correct index
   - `merkle_proof` = valid proof
   - **`confirmations = 0`**
4. The contract evaluates `(H - H + 1) >= 0` → `true`. Returns `true`.
5. Downstream NEAR contract releases wrapped BTC / minted tokens to the attacker.
6. Attacker broadcasts a conflicting Bitcoin transaction (double-spend) or a miner produces a competing block at height `H` that excludes the original transaction. The light client accepts the reorg; block `B` is removed from the main chain.
7. Attacker retains the NEAR-side payout while the Bitcoin-side transaction is erased.

The root cause is the absence of a lower-bound check on `args.confirmations` at: [1](#0-0) 
combined with the always-true arithmetic at: [2](#0-1)

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
