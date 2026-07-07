### Title
Division-by-Zero in `PerpEngine.socializeSubaccount` Permanently Blocks Liquidation Finalization - (File: `core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount` unconditionally divides by `state.openInterest` when a liquidatee has residual negative `vQuoteBalance` that insurance cannot fully cover. If `openInterest` is zero for that perp market at the time of finalization, the division reverts, permanently blocking the `_finalizeSubaccount` path and leaving the protocol with unrecoverable bad debt.

---

### Finding Description

The liquidation finalization path in `ClearinghouseLiq._finalizeSubaccount` is triggered when `txn.productId == type(uint32).max`. After verifying all perp `amount` fields are zero and settling positive PnL, it calls:

```solidity
v.insurance = perpEngine.socializeSubaccount(txn.liquidatee, v.insurance);
``` [1](#0-0) 

Inside `PerpEngine.socializeSubaccount`, for each perp product where `balance.vQuoteBalance < 0` and insurance is insufficient to cover it, the code attempts to spread the loss across all open-interest holders:

```solidity
int128 fundingPerShare = -balance.vQuoteBalance.div(
    state.openInterest
);
``` [2](#0-1) 

There is **no guard** checking `state.openInterest != 0` before this division. If `openInterest` is zero, the `MathSD21x18.div` call reverts, causing the entire `liquidateSubaccountImpl` delegatecall to revert.

The `_finalizeSubaccount` function itself already requires `balance.amount == 0` for all perps before calling `socializeSubaccount`: [3](#0-2) 

This means the liquidatee's own position is already closed and contributes zero to `openInterest`. If no other participants hold open positions in that perp market, `openInterest` is zero at the moment of the call. The protocol even defines `ERR_NO_OPEN_INTEREST = "NOI"` in `Errors.sol`, acknowledging this edge case exists, but `socializeSubaccount` never checks for it: [4](#0-3) 

---

### Impact Explanation

When `openInterest == 0` and the liquidatee has residual negative `vQuoteBalance` that exceeds available insurance, every call to `liquidateSubaccountImpl` with `productId == type(uint32).max` (the finalization transaction) reverts. The subaccount can never be finalized. The negative `vQuoteBalance` (bad debt) is permanently stuck in the protocol with no recovery path. Insurance funds cannot be applied, and socialization cannot proceed.

---

### Likelihood Explanation

This is reachable in any low-liquidity perp market where the liquidatee is the last (or only) participant with an open position. Once their position is closed during prior liquidation steps (required before finalization), `openInterest` drops to zero. If the liquidatee accumulated negative PnL exceeding insurance, the finalization call will always revert. This is not a theoretical edge case — it is structurally guaranteed to occur in any perp market that reaches zero open interest while a subaccount with negative `vQuoteBalance` is being finalized.

---

### Recommendation

Add a zero-check for `state.openInterest` before dividing in `PerpEngine.socializeSubaccount`. If `openInterest == 0`, there are no counterparties to socialize the loss against; the loss should either be absorbed entirely by insurance or written off. For example:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No open interest to socialize against; absorb via insurance or write off
        balance.vQuoteBalance = 0;
    } else {
        int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
        balance.vQuoteBalance = 0;
    }
}
```

---

### Proof of Concept

1. A perp market (e.g., `perpId = 2`) has only one participant: the liquidatee subaccount `A`.
2. Subaccount `A` opens a long position, accumulates negative `vQuoteBalance` (e.g., `-1000e18`) due to funding payments, then closes the position (`amount = 0`). `openInterest` is now `0`.
3. Subaccount `A`'s maintenance health drops below zero (e.g., due to quote balance being negative).
4. A liquidator calls `liquidateSubaccount` with `productId = type(uint32).max` to finalize.
5. `_finalizeSubaccount` passes the `balance.amount == 0` check for all perps.
6. `perpEngine.socializeSubaccount(A, insurance)` is called.
7. For `perpId = 2`: `balance.vQuoteBalance = -1000e18`, `insurance < 1000e18`, so `balance.vQuoteBalance` remains negative after insurance cover.
8. The code reaches `(-balance.vQuoteBalance).div(state.openInterest)` where `state.openInterest == 0`.
9. `MathSD21x18.div` reverts on division by zero.
10. The entire `liquidateSubaccountImpl` delegatecall reverts. Finalization is permanently impossible. Bad debt is locked in the protocol. [5](#0-4) [6](#0-5)

### Citations

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

**File:** core/contracts/common/Errors.sol (L55-56)
```text
// Socializing product with no open interest
string constant ERR_NO_OPEN_INTEREST = "NOI";
```
