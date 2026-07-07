### Title
Zero-Weight Spot Balances Not Transferred to Liquidator During Subaccount Finalization — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`ClearinghouseLiq._finalizeSubaccount` explicitly skips all spot products where `longWeightInitialX18 == 0` in every balance check and never transfers those balances to the liquidator. `SpotEngine.socializeSubaccount` only clears **negative** balances. As a result, a liquidatee retains positive zero-weight spot balances after full liquidation while the insurance fund or liquidator absorbs the corresponding quote deficit — a direct asset loss for the protocol/liquidator and an unearned asset retention for the liquidatee.

---

### Finding Description

`_finalizeSubaccount` is invoked when `txn.productId == type(uint32).max`, signalling a full subaccount close-out. The function enforces that all non-zero-weight spot assets are at or below zero and all perp positions are closed before proceeding. However, zero-weight spot products are unconditionally skipped at two separate guard points:

**First guard — pre-condition check (lines 301–311):** [1](#0-0) 

**Second guard — post-PnL-settlement check (lines 373–383):** [2](#0-1) 

After both guards, the function calls `perpEngine.socializeSubaccount` and then `spotEngine.socializeSubaccount`: [3](#0-2) 

`SpotEngine.socializeSubaccount` iterates all product IDs but only acts on **negative** balances: [4](#0-3) 

Positive zero-weight spot balances are therefore never touched — not transferred to the liquidator (`txn.sender`), not zeroed out, not socialized. The liquidatee's subaccount exits finalization still holding those assets.

The same skip pattern appears in `_assertCanLiquidateLiability`, confirming that zero-weight spot products are a recognized, intentional category in the protocol that can carry positive balances into the finalization step: [5](#0-4) 

---

### Impact Explanation

Two concrete harms mirror the DYAD analog exactly:

1. **Liquidator / insurance fund loss**: The negative quote balance of the liquidatee is covered by the insurance fund (or socialized across depositors), but the offsetting positive zero-weight spot assets that should compensate for that deficit are never moved to the liquidator. The liquidator pays (or the fund absorbs) without receiving the corresponding collateral.

2. **Liquidatee retains assets after full liquidation**: The invariant that a fully finalized subaccount holds zero net assets is broken. The liquidatee keeps positive zero-weight spot balances (e.g., NLP tokens representing real redeemable value) after the protocol considers the account closed.

The `NLP_PRODUCT_ID` product is a concrete candidate: it is a spot product with a dedicated lock/unlock queue and real quote-denominated redemption value (`burnNlp` returns quote), and it receives special-case handling in `updateBalance` that is distinct from ordinary spot products: [6](#0-5) 

If NLP is configured with `longWeightInitial = 0` (consistent with it not contributing to borrowing capacity), a liquidatee holding NLP tokens would retain them through finalization.

---

### Likelihood Explanation

The trigger requires:
- A subaccount that holds a positive balance in at least one zero-weight spot product at the time of finalization.
- The subaccount must be under maintenance health (liquidatable).

Both conditions are reachable by any unprivileged user: a trader can deposit collateral, mint NLP (or hold any other zero-weight spot token), take on leveraged positions, and become liquidatable through normal market movement. No admin action or special privilege is required to reach the vulnerable code path. The finalization call (`productId = type(uint32).max`) is a standard liquidation transaction routed through `Endpoint → Clearinghouse.liquidateSubaccount → delegatecall → ClearinghouseLiq.liquidateSubaccountImpl`: [7](#0-6) 

---

### Recommendation

Inside `_finalizeSubaccount`, after settling PnL and before (or instead of) relying solely on `socializeSubaccount`, iterate all spot product IDs and transfer any **positive** zero-weight spot balances from the liquidatee to the liquidator (`txn.sender`):

```solidity
for (uint32 i = 1; i < v.spotIds.length; ++i) {
    uint32 spotId = v.spotIds[i];
    if (spotEngine.getRisk(spotId).longWeightInitialX18 != 0) {
        continue; // already handled by prior checks
    }
    ISpotEngine.Balance memory balance = spotEngine.getBalance(spotId, txn.liquidatee);
    if (balance.amount > 0) {
        spotEngine.updateBalance(spotId, txn.liquidatee, -balance.amount);
        spotEngine.updateBalance(spotId, txn.sender, balance.amount);
    }
}
```

This mirrors the fix recommended in the DYAD report and ensures the liquidatee exits finalization with zero assets across all product categories.

---

### Proof of Concept

1. Alice deposits USDC and mints NLP tokens (zero-weight spot product). She also opens a leveraged perp position.
2. Market moves against Alice; her maintenance health drops below zero.
3. Bob (liquidator) liquidates Alice's perp positions one by one until `amount == 0` for all perps.
4. Bob calls `liquidateSubaccount` with `productId = type(uint32).max` to finalize.
5. `_finalizeSubaccount` skips Alice's NLP balance at lines 303–305 and 379–381.
6. `spotEngine.socializeSubaccount` only clears Alice's negative USDC balance; NLP balance is untouched.
7. After finalization: Alice's USDC quote balance is zero (covered by insurance/socialization), but Alice still holds her NLP tokens. Bob received no NLP despite the insurance fund absorbing Alice's deficit.

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L235-245)
```text
        for (uint32 i = 1; i < spotIds.length; ++i) {
            uint32 spotId = spotIds[i];
            if (spotEngine.getRisk(spotId).longWeightInitialX18 == 0) {
                continue;
            }
            ISpotEngine.Balance memory balance = spotEngine.getBalance(
                spotId,
                txn.liquidatee
            );
            require(balance.amount <= 0, ERR_NOT_LIQUIDATABLE_LIABILITIES);
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L301-311)
```text
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
```

**File:** core/contracts/ClearinghouseLiq.sol (L373-383)
```text
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
```

**File:** core/contracts/ClearinghouseLiq.sol (L386-409)
```text
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
```

**File:** core/contracts/SpotEngine.sol (L193-195)
```text
        if (productId == NLP_PRODUCT_ID) {
            handleNlpLockedBalance(subaccount, amountDelta);
        }
```

**File:** core/contracts/SpotEngine.sol (L243-276)
```text
    function socializeSubaccount(bytes32 subaccount) external {
        require(msg.sender == address(_clearinghouse), ERR_UNAUTHORIZED);

        uint32[] memory _productIds = getProductIds();
        for (uint128 i = 0; i < _productIds.length; ++i) {
            uint32 productId = _productIds[i];

            State memory state = states[productId];
            Balance memory balance = balanceNormalizedToBalance(
                state,
                balances[productId][subaccount]
            );
            if (balance.amount < 0) {
                int128 totalDeposited = state.totalDepositsNormalized.mul(
                    state.cumulativeDepositsMultiplierX18
                );

                state.cumulativeDepositsMultiplierX18 = (totalDeposited +
                    balance.amount).div(state.totalDepositsNormalized);

                require(state.cumulativeDepositsMultiplierX18 > 0);

                state.totalBorrowsNormalized += balance.amount.div(
                    state.cumulativeBorrowsMultiplierX18
                );

                _setBalanceAndUpdateBitmap(
                    productId,
                    subaccount,
                    BalanceNormalized({amountNormalized: 0})
                );
                _setState(productId, state);
            }
        }
```

**File:** core/contracts/Clearinghouse.sol (L644-662)
```text
    function liquidateSubaccount(IEndpoint.LiquidateSubaccount calldata txn)
        external
        virtual
        onlyEndpoint
    {
        bytes4 liquidateSubaccountSelector = bytes4(
            keccak256(
                "liquidateSubaccountImpl((bytes32,bytes32,uint32,bool,int128,uint64))"
            )
        );
        bytes memory liquidateSubaccountCall = abi.encodeWithSelector(
            liquidateSubaccountSelector,
            txn
        );
        (bool success, bytes memory result) = clearinghouseLiq.delegatecall(
            liquidateSubaccountCall
        );
        require(success, string(result));
    }
```
