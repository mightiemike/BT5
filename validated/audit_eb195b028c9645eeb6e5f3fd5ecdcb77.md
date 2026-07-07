### Title
Stale `lastLiquidationFees` Causes Insurance Understatement in `_finalizeSubaccount`, Spreading Excess Socialization Loss to Participants — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`lastLiquidationFees` is a global persistent storage variable in `ClearinghouseStorage`. It is only written in `_handleLiquidationPayment`. When `liquidateSubaccountImpl` is called with `productId == type(uint32).max`, execution branches directly into `_finalizeSubaccount` and returns early — `_handleLiquidationPayment` is never called. As a result, `lastLiquidationFees` retains whatever value was set by the most recent prior liquidation of **any** subaccount. `_finalizeSubaccount` then subtracts this stale value from `insurance` before passing it to `perpEngine.socializeSubaccount`, causing the socializer to receive less insurance than is actually available, and spreading more loss to other participants than necessary.

---

### Finding Description

**Storage layout:**

`lastLiquidationFees` is declared as a plain `int128` in `ClearinghouseStorage` with no per-subaccount scoping: [1](#0-0) 

It is written exactly once, at the end of `_handleLiquidationPayment`: [2](#0-1) 

**The finalization path skips `_handleLiquidationPayment` entirely:**

When `txn.productId == type(uint32).max`, `liquidateSubaccountImpl` calls `_finalizeSubaccount`, which returns `true`, and the function returns before ever reaching `_handleLiquidationPayment`: [3](#0-2) 

**`_finalizeSubaccount` uses the stale global value:** [4](#0-3) 

`v.insurance` is reduced by `lastLiquidationFees` (line 369) before being passed to `perpEngine.socializeSubaccount` (line 386). At the end, `lastLiquidationFees` is added back and stored: [5](#0-4) 

The net effect on the `insurance` storage variable is zero (subtract then add). But `socializeSubaccount` was called with `insurance - lastLiquidationFees` instead of `insurance`. The stale `lastLiquidationFees` amount is withheld from the socializer, causing it to spread more loss to other participants than necessary.

**`socializeSubaccount` in `PerpEngine` uses the passed insurance to cover negative vQuoteBalances before socializing:** [6](#0-5) 

If the passed insurance is understated by `lastLiquidationFees`, the socializer covers less of the negative vQuoteBalance from insurance and spreads more loss via `cumulativeFundingLongX18`/`cumulativeFundingShortX18` adjustments.

---

### Impact Explanation

- `socializeSubaccount` receives `insurance - lastLiquidationFees` (stale) instead of `insurance`.
- The shortfall (`lastLiquidationFees`) is not used to cover the liquidatee's negative vQuoteBalance.
- That shortfall is instead socialized across all open-interest holders via funding rate adjustments.
- The insurance fund retains `lastLiquidationFees` that should have been consumed.
- The invariant "insurance must be fully applied before socialization" is broken: participants absorb losses that the insurance fund had the capacity to cover.

---

### Likelihood Explanation

The path is reachable by any liquidator. The normal liquidation workflow is: multiple `liquidateSubaccountImpl` calls with real `productId` values (each setting `lastLiquidationFees`), followed by a final call with `productId == type(uint32).max`. Because `lastLiquidationFees` is global and not per-subaccount, a liquidator can:

1. Liquidate subaccount A (sets `lastLiquidationFees = X`, a large value).
2. Immediately finalize subaccount B with `productId == type(uint32).max` — `lastLiquidationFees` is still `X`.
3. Subaccount B's socialization uses `insurance - X` instead of `insurance`.

No privileged access is required. The sequencer processes `LiquidateSubaccount` transactions in order, and a liquidator controls the ordering of their own submitted transactions.

---

### Recommendation

Reset `lastLiquidationFees` to zero at the start of `_finalizeSubaccount` (or at the start of `liquidateSubaccountImpl` before the finalization branch), so that a finalization call that does not go through `_handleLiquidationPayment` does not inherit a stale fee value from a prior liquidation of a different subaccount:

```solidity
function _finalizeSubaccount(...) internal returns (bool) {
    if (txn.productId != type(uint32).max) {
        return false;
    }
    // Reset stale global fee so socialization uses full insurance
    lastLiquidationFees = 0;
    ...
}
```

Alternatively, scope `lastLiquidationFees` per-subaccount (e.g., `mapping(bytes32 => int128)`) so that a finalization of subaccount B cannot be contaminated by a prior liquidation of subaccount A.

---

### Proof of Concept

```
State before:
  insurance = 1000
  lastLiquidationFees = 0

Step 1: liquidateSubaccountImpl(liquidatee=A, productId=2, amount=...)
  → _handleLiquidationPayment runs
  → insurance += 50  → insurance = 1050
  → lastLiquidationFees = 50

Step 2: liquidateSubaccountImpl(liquidatee=B, productId=type(uint32).max)
  → _finalizeSubaccount runs
  → v.insurance = 1050
  → v.insurance -= lastLiquidationFees  → v.insurance = 1000  (stale 50 withheld)
  → perpEngine.socializeSubaccount(B, 1000)  ← only 1000 used, not 1050
  → v.insurance += 50  → v.insurance = 1050 - consumed_by_socializer + 50
  → insurance = v.insurance  (50 never consumed by socializer)

Result:
  - 50 units of insurance that could have covered B's losses were withheld
  - 50 units of loss were instead socialized across all open-interest holders
  - insurance retains 50 more than it should
  - Invariant broken: insurance was not fully applied before socialization
```

### Citations

**File:** core/contracts/ClearinghouseStorage.sol (L25-25)
```text
    int128 internal lastLiquidationFees;
```

**File:** core/contracts/ClearinghouseLiq.sol (L368-389)
```text
        v.insurance = insurance;
        v.insurance -= lastLiquidationFees;
        v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0;

        if (v.canLiquidateMore) {
            for (uint32 i = 1; i < v.spotIds.length; ++i) {
                uint32 spotId = v.spotIds[i];
                ISpotEngine.Balance memory balance = spotEngine.getBalance(
                    spotId,
                    txn.liquidatee
                );
                if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                    continue;
                }
                require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
            }
        }

        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );
```

**File:** core/contracts/ClearinghouseLiq.sol (L410-411)
```text
        v.insurance += lastLiquidationFees;
        insurance = v.insurance;
```

**File:** core/contracts/ClearinghouseLiq.sol (L579-586)
```text
        insurance += v.liquidationFees;

        // if insurance is not enough for making a subaccount healthy, we should
        // use all insurance to buy its liabilities, then socialize the subaccount
        // however, after the first step, insurance funds will be refilled a little bit
        // which blocks the second step, so we keep the fees of the last liquidation and
        // do not use this part in socialization to unblock it.
        lastLiquidationFees = v.liquidationFees;
```

**File:** core/contracts/ClearinghouseLiq.sol (L620-627)
```text
        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
            return;
        }
```

**File:** core/contracts/PerpEngine.sol (L154-172)
```text
            if (balance.vQuoteBalance < 0) {
                int128 insuranceCover = MathHelper.min(
                    insurance,
                    -balance.vQuoteBalance
                );
                insurance -= insuranceCover;
                balance.vQuoteBalance += insuranceCover;
                state.availableSettle += insuranceCover;

                // actually socialize if still not enough
                if (balance.vQuoteBalance < 0) {
                    // socialize across all other participants
                    int128 fundingPerShare = -balance.vQuoteBalance.div(
                        state.openInterest
                    );
                    state.cumulativeFundingLongX18 += fundingPerShare;
                    state.cumulativeFundingShortX18 -= fundingPerShare;
                    balance.vQuoteBalance = 0;
                }
```
