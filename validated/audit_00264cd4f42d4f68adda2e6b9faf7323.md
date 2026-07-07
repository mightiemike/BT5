### Title
Division-by-Zero in `PerpEngine.socializeSubaccount` When `openInterest == 0` Permanently Blocks Subaccount Finalization — (`core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` unconditionally divides by `state.openInterest` when a product's `vQuoteBalance` is still negative after insurance coverage. If the liquidatee is the sole historical holder of a product and their position has already been closed (`balance.amount == 0`), `openInterest` is zero. The `MathSD21x18.div` call reverts with `"DBZ"`, permanently blocking finalization and locking any remaining collateral in the subaccount.

---

### Finding Description

In `PerpEngine.socializeSubaccount`, after attempting to cover a negative `vQuoteBalance` with insurance, the code socializes the remaining loss across all open-interest holders:

```solidity
if (balance.vQuoteBalance < 0) {
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest   // ← can be 0
    );
``` [1](#0-0) 

`MathSD21x18.div` explicitly reverts on a zero denominator:

```solidity
function div(int128 x, int128 y) internal pure returns (int128) {
    unchecked {
        require(y != 0, ERR_DIV_BY_ZERO);
``` [2](#0-1) 

`state.openInterest` is the sum of `abs(balance.amount)` across all subaccounts for a product. In `_updateBalance`, the contribution of a subaccount with `balance.amount == 0` is exactly zero:

```solidity
state.openInterest -= balance.amount.abs();   // pre: removes old contribution
// ...
balance.amount += balanceDelta;               // amount stays 0
// post: adds 0 back
``` [3](#0-2) 

Therefore, if the liquidatee was the **sole** holder of a product and their position was already closed (`amount == 0`), `state.openInterest` stored in contract storage is 0 for that product.

The finalization path in `_finalizeSubaccount` **requires** `balance.amount == 0` for every perp product before calling `socializeSubaccount`:

```solidity
require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
``` [4](#0-3) 

Then unconditionally calls:

```solidity
v.insurance = perpEngine.socializeSubaccount(txn.liquidatee, v.insurance);
``` [5](#0-4) 

A subaccount can have `amount == 0` but `vQuoteBalance < 0` when a position was closed at a realized loss (the vQuoteBalance accumulates PnL through `_updateBalance`). If the quote balance is also non-positive (preventing the negative-pnl settlement loop at lines 346–366 from clearing it), the negative `vQuoteBalance` survives into `socializeSubaccount`, triggering the revert. [6](#0-5) 

---

### Impact Explanation

Every call to `liquidateSubaccountImpl` with `productId == type(uint32).max` (the finalization trigger) reverts. The subaccount can never be finalized. Any remaining quote balance or other assets in the subaccount are permanently locked — no admin escape hatch or alternative finalization path exists in the scoped code.

---

### Likelihood Explanation

The condition is reachable in normal protocol operation: a user opens a perp position, is the only participant in that market, closes the position at a loss (leaving `amount == 0`, `vQuoteBalance < 0`), then becomes unhealthy due to the negative vQuoteBalance. A liquidator attempting finalization will always revert. The scenario requires no privileged access and no unusual token behavior.

---

### Recommendation

Add a zero-check guard before the division in `socializeSubaccount`. If `state.openInterest == 0` and `balance.vQuoteBalance < 0`, the loss cannot be socialized (there are no other participants to absorb it); it should instead be absorbed entirely by the insurance fund, or written off:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No open interest to socialize against; absorb via insurance or write off
        state.availableSettle += balance.vQuoteBalance; // adjust as appropriate
        balance.vQuoteBalance = 0;
    } else {
        int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
        balance.vQuoteBalance = 0;
    }
}
``` [7](#0-6) 

---

### Proof of Concept

1. Deploy the protocol on a local Hardhat fork.
2. Create subaccount A; open a long perp position in product P (A is the sole holder → `openInterest > 0`).
3. Close A's position at a loss via the offchain exchange: `balance.amount → 0`, `balance.vQuoteBalance < 0`, `openInterest → 0`.
4. Drain A's spot quote balance to zero (or ensure it is already ≤ 0) so the negative-pnl settlement loop in `_finalizeSubaccount` cannot clear the vQuoteBalance.
5. Ensure A's maintenance health is negative (the negative vQuoteBalance contributes negatively to health).
6. Call `liquidateSubaccountImpl` with `productId = type(uint32).max` from a liquidator.
7. Assert the transaction reverts with `"DBZ"`.
8. Confirm no alternative path exists to finalize A or recover its collateral.

### Citations

**File:** core/contracts/PerpEngine.sol (L164-172)
```text
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

**File:** core/contracts/libraries/MathSD21x18.sol (L62-65)
```text
    function div(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            require(y != 0, ERR_DIV_BY_ZERO);
            int256 result = (int256(x) * ONE_X18) / y;
```

**File:** core/contracts/PerpEngineState.sol (L30-51)
```text
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

**File:** core/contracts/ClearinghouseLiq.sol (L319-319)
```text
            require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
```

**File:** core/contracts/ClearinghouseLiq.sol (L346-366)
```text
        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            if (balance.vQuoteBalance < 0 && quoteBalance.amount > 0) {
                int128 canSettle = MathHelper.max(
                    balance.vQuoteBalance,
                    -quoteBalance.amount
                );
                _settlePnlAgainstLiquidator(
                    txn,
                    perpId,
                    canSettle,
                    spotEngine,
                    perpEngine
                );
                quoteBalance.amount += canSettle;
            }
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L386-389)
```text
        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );
```
