### Title
Division by Zero in `socializeSubaccount` When `openInterest` Is Zero — (File: `core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` divides by `state.openInterest` without a prior zero-guard. When a perp market has no open positions (`openInterest == 0`) but the subaccount being finalized still carries a negative `vQuoteBalance`, the call reverts with `"DBZ"`, permanently blocking the liquidation finalization path and leaving bad debt unresolved.

---

### Finding Description

In `PerpEngine.socializeSubaccount`, after exhausting insurance coverage, the remaining deficit is spread across all open-interest holders via:

```solidity
int128 fundingPerShare = -balance.vQuoteBalance.div(
    state.openInterest
);
``` [1](#0-0) 

`MathSD21x18.div` unconditionally enforces `require(y != 0, "DBZ")`: [2](#0-1) 

No guard exists in `socializeSubaccount` to skip or handle the `openInterest == 0` case before reaching this division.

The caller of `socializeSubaccount` is `_finalizeSubaccount` in `ClearinghouseLiq.sol`: [3](#0-2) 

Before that call, `_finalizeSubaccount` requires that every perp `balance.amount == 0`: [4](#0-3) 

Because the liquidatee's `balance.amount` is zero for all perps at this point, `state.openInterest` returned by `getStateAndBalance` reflects only the positions of **all other participants**. If those participants have also closed their positions, `state.openInterest == 0`.

A subaccount can legitimately reach `balance.amount == 0` with `balance.vQuoteBalance < 0`: closing a position crystallises accumulated funding losses into `vQuoteBalance` without zeroing it. This is the normal path through `_updateBalance`: [5](#0-4) 

---

### Impact Explanation

When the revert fires, `_finalizeSubaccount` cannot complete. The insolvent subaccount is never cleaned up: its negative `vQuoteBalance` is never socialized, the insurance fund is never applied, and the subaccount remains in the system with unresolved bad debt. This corrupts the protocol's accounting state — the deficit is neither absorbed nor distributed — and permanently blocks the finalization entry point for that subaccount.

---

### Likelihood Explanation

The condition is realistic in any low-activity perp market or during market wind-down. It requires:

1. A subaccount that closed its position but retained a negative `vQuoteBalance` (e.g., from funding payments).
2. All other participants in that market having also closed their positions, leaving `openInterest == 0`.

Both conditions arise naturally without any privileged action. A sophisticated actor could also deliberately engineer this state by being the sole remaining participant in a thin market before becoming insolvent.

---

### Recommendation

Add an explicit zero-check for `state.openInterest` before the division inside `socializeSubaccount`. If `openInterest == 0`, there are no counterparties to absorb the loss; the deficit should be fully covered by the insurance fund or written off directly:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No open interest to socialize against; write off the deficit
        balance.vQuoteBalance = 0;
    } else {
        int128 fundingPerShare = -balance.vQuoteBalance.div(
            state.openInterest
        );
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
        balance.vQuoteBalance = 0;
    }
}
```

---

### Proof of Concept

1. A perp market (productId `X`) is deployed with a single active participant — the future liquidatee (subaccount `A`).
2. `A` opens a long position; funding payments accumulate, making `balance.vQuoteBalance < 0`.
3. `A` closes the position: `balance.amount` becomes `0`, but `balance.vQuoteBalance` remains negative.
4. No other participant holds an open position in market `X`, so `states[X].openInterest == 0`.
5. `A`'s quote balance falls below zero (e.g., from spot liabilities), making it insolvent.
6. A liquidator calls `liquidateSubaccountImpl` with `txn.productId = type(uint32).max` to trigger finalization.
7. `_finalizeSubaccount` passes the `balance.amount == 0` check for all perps and calls `perpEngine.socializeSubaccount(txn.liquidatee, v.insurance)`.
8. Inside `socializeSubaccount`, `balance.vQuoteBalance < 0` is true and insurance is insufficient, so execution reaches `(-balance.vQuoteBalance).div(state.openInterest)` with `state.openInterest == 0`.
9. `MathSD21x18.div` reverts with `"DBZ"`.
10. The entire `liquidateSubaccountImpl` call reverts; the insolvent subaccount can never be finalized and its bad debt is permanently unresolved.

### Citations

**File:** core/contracts/PerpEngine.sol (L164-168)
```text
                if (balance.vQuoteBalance < 0) {
                    // socialize across all other participants
                    int128 fundingPerShare = -balance.vQuoteBalance.div(
                        state.openInterest
                    );
```

**File:** core/contracts/libraries/MathSD21x18.sol (L62-65)
```text
    function div(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            require(y != 0, ERR_DIV_BY_ZERO);
            int256 result = (int256(x) * ONE_X18) / y;
```

**File:** core/contracts/ClearinghouseLiq.sol (L313-320)
```text
        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L386-389)
```text
        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );
```

**File:** core/contracts/PerpEngineState.sol (L29-51)
```text
        // pre update
        state.openInterest -= balance.amount.abs();
        int128 cumulativeFundingAmountX18 = (balance.amount > 0)
            ? state.cumulativeFundingLongX18
            : state.cumulativeFundingShortX18;
        int128 diffX18 = cumulativeFundingAmountX18 -
            balance.lastCumulativeFundingX18;
        int128 deltaQuote = vQuoteDelta - diffX18.mul(balance.amount);

        // apply delta
        balance.amount += balanceDelta;

        // apply vquote
        balance.vQuoteBalance += deltaQuote;

        // post update
        if (balance.amount > 0) {
            state.openInterest += balance.amount;
            balance.lastCumulativeFundingX18 = state.cumulativeFundingLongX18;
        } else {
            state.openInterest -= balance.amount;
            balance.lastCumulativeFundingX18 = state.cumulativeFundingShortX18;
        }
```
