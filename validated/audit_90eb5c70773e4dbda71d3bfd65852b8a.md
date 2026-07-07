### Title
Division by Zero in `socializeSubaccount` Blocks Liquidation Finalization When `openInterest` Is Zero — (File: `core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` divides by `state.openInterest` without checking whether it is zero. Because `_finalizeSubaccount` requires all perp position amounts to be zero before calling `socializeSubaccount`, the subaccount contributes nothing to `openInterest`. If no other participants hold open positions in that product at the time of finalization, `state.openInterest == 0` and the division reverts with `"DBZ"`, permanently blocking liquidation finalization for that subaccount.

---

### Finding Description

In `PerpEngine.socializeSubaccount`, after exhausting insurance coverage, the contract attempts to spread the remaining negative `vQuoteBalance` across all open-interest holders:

```solidity
// PerpEngine.sol lines 164–171
if (balance.vQuoteBalance < 0) {
    // socialize across all other participants
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest   // <-- can be zero
    );
    state.cumulativeFundingLongX18 += fundingPerShare;
    state.cumulativeFundingShortX18 -= fundingPerShare;
    balance.vQuoteBalance = 0;
}
``` [1](#0-0) 

`MathSD21x18.div` enforces `require(y != 0, ERR_DIV_BY_ZERO)`, so a zero denominator causes a hard revert: [2](#0-1) 

The caller is `_finalizeSubaccount` in `ClearinghouseLiq.sol`, which enforces that every perp `balance.amount == 0` before invoking `socializeSubaccount`: [3](#0-2) 

```solidity
// ClearinghouseLiq.sol line 386
v.insurance = perpEngine.socializeSubaccount(txn.liquidatee, v.insurance);
``` [4](#0-3) 

Inside `socializeSubaccount`, `getStateAndBalance` is called, which internally calls `_updateBalance(state, balance, 0, 0)`. Because `balance.amount == 0` (enforced above), `_updateBalance` subtracts and re-adds zero — leaving `state.openInterest` equal to the sum of all *other* participants' positions: [5](#0-4) 

If no other participant holds an open position in that product, `state.openInterest == 0`, and the `.div(state.openInterest)` call reverts.

Notably, `Errors.sol` already defines `ERR_NO_OPEN_INTEREST = "NOI"` with the comment `// Socializing product with no open interest`, confirming the developers anticipated this scenario but did not guard against it in `socializeSubaccount`: [6](#0-5) 

---

### Impact Explanation

The revert propagates up through `_finalizeSubaccount` → `liquidateSubaccountImpl`, causing the entire liquidation transaction to fail. The insolvent subaccount cannot be finalized: its negative `vQuoteBalance` (bad debt) cannot be socialized, the subaccount remains in an unresolvable state, and the protocol cannot reclaim or redistribute the loss. This is a **liquidation/settlement bypass** and a **bad-debt lock**: the protocol's socialization mechanism — its last line of defense against insolvency — is rendered inoperable for the affected product.

---

### Likelihood Explanation

The condition requires:
1. A subaccount undergoing finalization with `balance.amount == 0` but `balance.vQuoteBalance < 0` (closed position with residual negative PnL — a normal outcome of liquidation).
2. Insurance is insufficient to cover the deficit (also a normal outcome in stressed markets).
3. No other participant holds an open position in that specific perp product at that moment.

Condition 3 is most restrictive but realistic for newly listed products, low-liquidity products, or products where all other participants have also closed positions. It is also reachable in a targeted griefing scenario where a single participant opens and closes a position to leave a product with zero open interest before a finalization is attempted.

---

### Recommendation

Add a zero-check for `state.openInterest` before the division in `socializeSubaccount`. If `openInterest` is zero there are no other participants to socialize against; the bad debt cannot be distributed and should either be absorbed by the insurance fund, written off, or cause a revert with the already-defined `ERR_NO_OPEN_INTEREST` error:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No open interest to socialize against; revert or handle explicitly
        revert(ERR_NO_OPEN_INTEREST);
    }
    int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
    state.cumulativeFundingLongX18 += fundingPerShare;
    state.cumulativeFundingShortX18 -= fundingPerShare;
    balance.vQuoteBalance = 0;
}
```

---

### Proof of Concept

1. Product `perpId = 2` is listed. Only one subaccount `A` opens a long position of size `S` at price `P`. `state.openInterest = S`.
2. Price drops. Subaccount `A` is liquidated: its position is closed (`balance.amount = 0`) but `balance.vQuoteBalance = -D` (negative PnL, `D > 0`).
3. All other participants have also exited; `state.openInterest = 0` (no other open positions).
4. Insurance fund is insufficient: `insurance < D`.
5. Liquidator calls `liquidateSubaccountImpl` with `txn.productId = type(uint32).max` to trigger finalization.
6. `_finalizeSubaccount` passes the `balance.amount == 0` check and calls `perpEngine.socializeSubaccount(txn.liquidatee, insurance)`.
7. Inside `socializeSubaccount`, after partial insurance cover, `balance.vQuoteBalance < 0` still holds.
8. `(-balance.vQuoteBalance).div(state.openInterest)` → `div(x, 0)` → `require(0 != 0, "DBZ")` → **revert**.
9. The entire `liquidateSubaccountImpl` call reverts. The subaccount cannot be finalized. Bad debt is permanently locked. [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/PerpEngine.sol (L141-178)
```text
    function socializeSubaccount(bytes32 subaccount, int128 insurance)
        external
        returns (int128)
    {
        require(msg.sender == address(_clearinghouse), ERR_UNAUTHORIZED);

        uint32[] memory _productIds = getProductIds();
        for (uint128 i = 0; i < _productIds.length; ++i) {
            uint32 productId = _productIds[i];
            (State memory state, Balance memory balance) = getStateAndBalance(
                productId,
                subaccount
            );
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
    }
```

**File:** core/contracts/libraries/MathSD21x18.sol (L62-68)
```text
    function div(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            require(y != 0, ERR_DIV_BY_ZERO);
            int256 result = (int256(x) * ONE_X18) / y;
            require(result >= MIN_X18 && result <= MAX_X18, ERR_OVERFLOW);
            return int128(result);
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L279-413)
```text
    function _finalizeSubaccount(
        IEndpoint.LiquidateSubaccount calldata txn,
        ISpotEngine spotEngine,
        IPerpEngine perpEngine
    ) internal returns (bool) {
        if (txn.productId != type(uint32).max) {
            return false;
        }
        // check whether the subaccount can be finalized:
        // - all perps positions have closed
        // - all spread positions have closed
        // - all spot assets have closed
        // - all positive pnls have been settled

        FinalizeVars memory v;

        v.spotIds = spotEngine.getProductIds();
        v.perpIds = perpEngine.getProductIds();

        require(v.spotIds[0] == QUOTE_PRODUCT_ID);

        // all spot assets (except USDC) must be closed out
        for (uint32 i = 1; i < v.spotIds.length; ++i) {
            uint32 spotId = v.spotIds[i];
            if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                continue;
            }
            ISpotEngine.Balance memory balance = spotEngine.getBalance(
                spotId,
                txn.liquidatee
            );
            require(balance.amount <= 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
        }

        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT);
        }

        // settle all positive pnl
        for (uint32 i = 0; i < v.perpIds.length; ++i) {
            uint32 perpId = v.perpIds[i];
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                perpId,
                txn.liquidatee
            );
            if (balance.vQuoteBalance > 0) {
                _settlePnlAgainstLiquidator(
                    txn,
                    perpId,
                    balance.vQuoteBalance,
                    spotEngine,
                    perpEngine
                );
            }
        }

        ISpotEngine.Balance memory quoteBalance = spotEngine.getBalance(
            QUOTE_PRODUCT_ID,
            txn.liquidatee
        );

        // settle all negative pnl until quote balance becomes 0
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
        return true;
    }
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

**File:** core/contracts/common/Errors.sol (L55-56)
```text
// Socializing product with no open interest
string constant ERR_NO_OPEN_INTEREST = "NOI";
```
