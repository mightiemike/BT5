### Title
Unbounded NLP Lock Queue Enables Liquidation OOG Bypass — (`File: core/contracts/SpotEngine.sol`)

---

### Summary

A user can accumulate an unbounded number of entries in their `nlpLockedBalanceQueues` by minting NLP at distinct oracle timestamps. Because `tryUnlockNlpBalance` is unconditionally invoked at the top of `handleNlpLockedBalance` — which is itself called on every `updateBalance` for `NLP_PRODUCT_ID` — a sufficiently large queue causes an out-of-gas revert in the liquidation path, permanently blocking liquidation of the attacker's NLP position.

---

### Finding Description

`SpotEngine.handleNlpLockedBalance` appends a new `NlpLockedBalance` entry to `nlpLockedBalanceQueues[subaccount]` whenever `mintNlp` is processed at an oracle timestamp that differs from the last queued entry's `unlockedAt` anchor:

```solidity
// SpotEngine.sol
queue.balances[queue.balanceCount] = NlpLockedBalance({
    balance: Balance({amount: amountDelta}),
    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
});
queue.balanceCount++;
```

There is no cap on `balanceCount`. [1](#0-0) 

Before any queue mutation, `tryUnlockNlpBalance(subaccount)` is called unconditionally:

```solidity
tryUnlockNlpBalance(subaccount);   // iterates queue — called regardless of amountDelta sign
if (amountDelta > 0) { ... }
else if (amountDelta < 0) { ... }
``` [2](#0-1) 

`tryUnlockNlpBalance` must iterate over the queue to drain entries whose `unlockedAt` has passed into `unlockedBalanceSum`. With a large queue of still-locked entries (all within the lock period), this iteration is O(n) and will OOG.

`updateBalance` for `NLP_PRODUCT_ID` routes through `handleNlpLockedBalance`:

```solidity
if (productId == NLP_PRODUCT_ID) {
    handleNlpLockedBalance(subaccount, amountDelta);
}
``` [3](#0-2) [4](#0-3) 

During liquidation, `_handleLiquidationPayment` calls `spotEngine.updateBalance(txn.productId, txn.liquidatee, -txn.amount)` for the liquidated product. When `txn.productId == NLP_PRODUCT_ID`, this triggers the OOG path: [5](#0-4) 

---

### Impact Explanation

A user who has accumulated a large NLP lock queue cannot have their NLP position liquidated. The `liquidateSubaccountImpl` call will OOG when it attempts to update the liquidatee's NLP balance, leaving an unhealthy subaccount with an NLP position permanently immune to liquidation. This corrupts the protocol's solvency invariant: the clearinghouse cannot enforce margin requirements on the attacker's account.

---

### Likelihood Explanation

Each `mintNlp` at a new oracle timestamp adds one queue entry. Oracle timestamps advance with every sequencer batch. A user submitting one small `mintNlp` per batch over thousands of blocks (feasible over days/weeks on a fast chain like Ink) accumulates thousands of locked entries — all within the lock period — before any are drained. The cost is only gas for many small NLP mints. No privileged access is required; any subaccount can call `mintNlp` through the standard sequencer path.

---

### Recommendation

Cap `balanceCount` at a sensible maximum (e.g., 100 entries) in `handleNlpLockedBalance`, reverting or merging aggressively if the cap is reached:

```solidity
require(queue.balanceCount < MAX_NLP_LOCK_ENTRIES, "NLP lock queue full");
```

Alternatively, ensure `tryUnlockNlpBalance` uses a persistent head-pointer so already-drained entries are never re-iterated, bounding per-call gas to newly-unlocked entries only.

---

### Proof of Concept

1. Attacker calls `mintNlp` (via sequencer) with a small amount at oracle time `T0`. Queue entry 0 added with `unlockedAt = T0 + LOCK_PERIOD`.
2. Attacker waits for the next oracle update (`T1 ≠ T0`), calls `mintNlp` again. Queue entry 1 added.
3. Repeat N times (N = several thousand, all within the lock period so `tryUnlockNlpBalance` drains nothing).
4. Attacker takes on debt (e.g., borrows USDC) to become undercollateralised.
5. Liquidator submits `LiquidateSubaccount` targeting `NLP_PRODUCT_ID`.
6. `_handleLiquidationPayment` → `spotEngine.updateBalance(NLP_PRODUCT_ID, liquidatee, ...)` → `handleNlpLockedBalance` → `tryUnlockNlpBalance` iterates N entries → OOG revert.
7. Liquidation is permanently blocked; the attacker's unhealthy position persists.

### Citations

**File:** core/contracts/SpotEngine.sol (L147-173)
```text
        tryUnlockNlpBalance(subaccount);
        if (amountDelta > 0) {
            NlpLockedBalanceQueue storage queue = nlpLockedBalanceQueues[
                subaccount
            ];
            if (
                queue.balanceCount > 0 &&
                queue.balances[queue.balanceCount - 1].unlockedAt ==
                getOracleTime() + NLP_LOCK_PERIOD
            ) {
                queue
                    .balances[queue.balanceCount - 1]
                    .balance
                    .amount += amountDelta;
            } else {
                queue.balances[queue.balanceCount] = NlpLockedBalance({
                    balance: Balance({amount: amountDelta}),
                    unlockedAt: getOracleTime() + NLP_LOCK_PERIOD
                });
                queue.balanceCount++;
            }
        } else if (amountDelta < 0) {
            Balance memory balanceSum = nlpLockedBalanceQueues[subaccount]
                .unlockedBalanceSum;
            balanceSum.amount += amountDelta;
            nlpLockedBalanceQueues[subaccount].unlockedBalanceSum = balanceSum;
        }
```

**File:** core/contracts/SpotEngine.sol (L193-195)
```text
        if (productId == NLP_PRODUCT_ID) {
            handleNlpLockedBalance(subaccount, amountDelta);
        }
```

**File:** core/contracts/SpotEngine.sol (L216-217)
```text
        if (productId == NLP_PRODUCT_ID) {
            handleNlpLockedBalance(subaccount, amountDelta);
```

**File:** core/contracts/ClearinghouseLiq.sol (L518-522)
```text
            spotEngine.updateBalance(
                txn.productId,
                txn.liquidatee,
                -txn.amount
            );
```
