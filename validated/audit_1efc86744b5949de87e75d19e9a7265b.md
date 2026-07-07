### Title
Insurance Storage Corrupted to `lastLiquidationFees` When `insurance < lastLiquidationFees` at Finalization — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`_finalizeSubaccount` subtracts `lastLiquidationFees` from `insurance` before passing the result to `perpEngine.socializeSubaccount`. When `insurance < lastLiquidationFees`, this produces a **negative** value. `PerpEngine.socializeSubaccount` mishandles a negative `insurance` argument: it consumes the negative value (driving `insurance` to 0) while simultaneously making the liquidatee's `vQuoteBalance` *more* negative, then socializes that inflated loss across all other perp participants. The function returns 0. Back in `_finalizeSubaccount`, `lastLiquidationFees` is unconditionally added back, setting `insurance = lastLiquidationFees` — a value larger than the true remaining reserve.

---

### Finding Description

**Relevant state variables** (`ClearinghouseLiq`):
- `insurance` — global reserve balance
- `lastLiquidationFees` — fees from the most recent liquidation step (global, overwritten on every liquidation)

**The broken sequence in `_finalizeSubaccount`** ( [1](#0-0) ):

```
v.insurance = insurance;            // e.g. 1
v.insurance -= lastLiquidationFees; // e.g. 1 - 10 = -9  ← NEGATIVE
...
v.insurance = perpEngine.socializeSubaccount(txn.liquidatee, v.insurance); // called with -9
...
v.insurance += lastLiquidationFees; // -9 consumed → 0 + 10 = 10
insurance = v.insurance;            // insurance = 10, but true reserve ≈ 0
```

**What `PerpEngine.socializeSubaccount` does with a negative `insurance`** ( [2](#0-1) ):

For each perp product where `balance.vQuoteBalance < 0`:
```
insuranceCover = min(insurance, -balance.vQuoteBalance)
              = min(-9, +20) = -9          // negative cover
insurance -= (-9)  →  insurance = 0        // negative consumed
balance.vQuoteBalance += (-9)  →  -29      // MORE negative than before
// then socialized: 29 spread to all other participants
```

The function returns `0`, not the original negative value. The negative insurance is silently absorbed, inflating the socialized loss borne by innocent perp participants.

**How `insurance < lastLiquidationFees` becomes reachable:**

`lastLiquidationFees` is a single global variable overwritten on every liquidation step ( [3](#0-2) ). The invariant `insurance >= lastLiquidationFees` holds immediately after a liquidation step, but it can be broken before finalization:

1. Liquidate subaccount A (last step): `insurance += 10`, `lastLiquidationFees = 10` → `insurance = 10`
2. Finalize subaccount C (another underwater account): `insurance` drops to `1` (C's liabilities consumed 9)
3. Finalize subaccount A: `v.insurance = 1 - 10 = -9` ← condition triggered

No special privileges are required; steps 1–3 are all reachable through the public `liquidateSubaccountImpl` endpoint.

---

### Impact Explanation

**Primary — insurance storage overstated:**
After finalization, `insurance` is set to `lastLiquidationFees` (e.g., 10) instead of the true remaining reserve (e.g., 0 or 1). All subsequent operations that read `insurance` — including future finalizations, socialization decisions, and health checks — operate on an inflated balance. The protocol believes it has more reserves than it actually does.

**Secondary — unjust socialization of other perp participants:**
The negative insurance value (`-9` in the example) is added to the liquidatee's `vQuoteBalance`, making it more negative by `|lastLiquidationFees - insurance|`. This inflated deficit is then spread across all open-interest holders via `cumulativeFundingLongX18` / `cumulativeFundingShortX18` ( [4](#0-3) ). Innocent participants bear losses they should not.

---

### Likelihood Explanation

The condition requires two concurrent liquidations where one finalization drains `insurance` below `lastLiquidationFees` before the other is finalized. This is a realistic scenario during periods of high market stress when multiple subaccounts are being liquidated simultaneously. No attacker-controlled parameters are needed beyond triggering standard liquidation and finalization transactions in the correct order.

---

### Recommendation

Guard against a negative `v.insurance` before passing it to `perpEngine.socializeSubaccount`. If `insurance < lastLiquidationFees`, clamp `v.insurance` to zero:

```solidity
v.insurance = insurance;
v.insurance -= lastLiquidationFees;
if (v.insurance < 0) {
    v.insurance = 0;
}
```

Alternatively, restructure the accounting so that `lastLiquidationFees` is only subtracted after socialization, or assert `insurance >= lastLiquidationFees` as a precondition and handle the edge case explicitly.

---

### Proof of Concept

```
State before:
  insurance = 1
  lastLiquidationFees = 10  (set by a prior liquidation step)

Attacker calls liquidateSubaccountImpl(txn) with txn.productId = type(uint32).max
  (triggering _finalizeSubaccount)

Trace:
  v.insurance = 1
  v.insurance -= 10  →  v.insurance = -9

  perpEngine.socializeSubaccount(liquidatee, -9):
    balance.vQuoteBalance = -20 (existing negative perp PnL)
    insuranceCover = min(-9, 20) = -9
    insurance -= (-9)  →  insurance = 0
    balance.vQuoteBalance += (-9)  →  -29
    -29 socialized across all other participants
    returns 0

  v.insurance = 0
  insuranceCover = min(0, -quoteBalance.amount) = 0  (no spot coverage)
  v.insurance <= 0  →  spotEngine.socializeSubaccount(liquidatee) called
  v.insurance += 10  →  v.insurance = 10
  insurance = 10   ← WRONG; true reserve = 0

Assert: insurance == 10, but should be 0 (or 1 at most).
Delta: insurance overstated by 10 (= lastLiquidationFees).
Other perp participants: socialized for an extra 9 units they should not bear.
```

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L368-411)
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

        // we can assure that quoteBalance must be non positive, because if quoteBalance.amount > 0,
        // there must be 1) no negative pnl in perps, and 2) no liabilities in spot after above actions.
        // however, in this case the liquidatee must be healthy and cannot pass the health check at
        // the beginning.
        int128 insuranceCover = MathHelper.min(
            v.insurance,
            -quoteBalance.amount
        );
        if (insuranceCover > 0) {
            v.insurance -= insuranceCover;
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                insuranceCover
            );
        }
        if (v.insurance <= 0) {
            spotEngine.socializeSubaccount(txn.liquidatee);
        }
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

**File:** core/contracts/PerpEngine.sol (L154-177)
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
                _setState(productId, state);
                _setBalanceAndUpdateBitmap(productId, subaccount, balance);
            }
        }
        return insurance;
```
