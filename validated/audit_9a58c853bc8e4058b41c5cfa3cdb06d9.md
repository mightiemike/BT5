### Title
Division by Zero in `socializeSubaccount()` Loop Blocks Liquidation Finalization When `openInterest == 0` — (File: `core/contracts/PerpEngine.sol`)

---

### Summary

`PerpEngine.socializeSubaccount()` contains a loop over all registered perp products. For each product where the liquidatee's `vQuoteBalance` remains negative after insurance coverage, it divides by `state.openInterest` to compute a per-share funding adjustment. If `openInterest` is zero — a reachable state when the liquidatee was the sole participant in a perp market — this division reverts, permanently blocking liquidation finalization for that subaccount.

---

### Finding Description

`PerpEngine.socializeSubaccount()` is called from `ClearinghouseLiq._finalizeSubaccount()` as the last step of the finalization liquidation path (triggered when `txn.productId == type(uint32).max`). By the time finalization is attempted, all perp position amounts (`balance.amount`) must already be zero — enforced by the `require(balance.amount == 0, ERR_NOT_FINALIZABLE_SUBACCOUNT)` check. However, `vQuoteBalance` can remain negative if the liquidatee's quote balance was insufficient to cover all negative PnL during the settlement loops.

Inside `socializeSubaccount`, for each product with a negative residual `vQuoteBalance` that exceeds available insurance, the protocol attempts to socialize the loss across all remaining open-interest holders:

```solidity
// PerpEngine.sol lines 164–170
if (balance.vQuoteBalance < 0) {
    // socialize across all other participants
    int128 fundingPerShare = -balance.vQuoteBalance.div(
        state.openInterest
    );
    state.cumulativeFundingLongX18 += fundingPerShare;
    state.cumulativeFundingShortX18 -= fundingPerShare;
    balance.vQuoteBalance = 0;
}
``` [1](#0-0) 

There is no guard on `state.openInterest == 0` before the division. `MathSD21x18.div` performs `a * ONE / b`; when `b == 0`, Solidity reverts with a division-by-zero panic.

**How `openInterest` reaches zero while `vQuoteBalance` remains negative:**

`openInterest` is maintained in `_updateBalance()`. When a position is closed (`balance.amount` becomes 0), the contribution is subtracted and nothing is added back:

```solidity
// PerpEngineState.sol lines 30–51
state.openInterest -= balance.amount.abs();
// ...
balance.amount += balanceDelta;
// ...
if (balance.amount > 0) {
    state.openInterest += balance.amount;
    // ...
} else {
    state.openInterest -= balance.amount;
    // ...
}
``` [2](#0-1) 

If the liquidatee was the sole participant in a perp market (or the last one remaining), closing their position drives `openInterest` to zero. Their `vQuoteBalance` can still be negative — representing unrealized loss from the position — because `vQuoteBalance` is updated independently of `openInterest`.

The full revert propagation chain is:

1. Sequencer submits `LiquidateSubaccount` with `productId == type(uint32).max`
2. `Clearinghouse.liquidateSubaccount()` → `delegatecall` → `ClearinghouseLiq.liquidateSubaccountImpl()` [3](#0-2) 

3. `liquidateSubaccountImpl()` calls `_finalizeSubaccount()` [4](#0-3) 

4. `_finalizeSubaccount()` calls `perpEngine.socializeSubaccount(txn.liquidatee, v.insurance)` [5](#0-4) 

5. `socializeSubaccount()` loops over all perp products and hits division by zero [6](#0-5) 

6. The entire transaction reverts. The insolvent subaccount cannot be finalized.

---

### Impact Explanation

When finalization is blocked, the insolvent subaccount's bad debt cannot be socialized. The negative `vQuoteBalance` remains permanently on the books. The insurance fund cannot be replenished from this subaccount. If multiple subaccounts in low-liquidity perp markets reach this state, the protocol accumulates unresolvable bad debt, directly threatening solvency: the total `vQuoteBalance` obligations across the system exceed the `availableSettle` liquidity, and the insurance fund cannot compensate.

The corrupted state is: `balance.vQuoteBalance < 0` for the liquidatee persists indefinitely, `insurance` is not correctly updated, and `_finalizeSubaccount` can never complete for this subaccount.

---

### Likelihood Explanation

The trigger requires:
1. A perp market where the liquidatee holds (or held) the only open position — realistic in newly listed or low-liquidity markets.
2. The liquidatee's position is closed in prior liquidation steps, driving `openInterest` to zero.
3. The liquidatee's `vQuoteBalance` remains negative after insurance coverage.

This is not a contrived edge case. Any protocol-listed perp market with a single active participant (e.g., a market maker who is also the only trader) satisfies condition 1. Condition 2 follows automatically from the finalization prerequisite. Condition 3 occurs whenever the position was loss-making and the quote balance plus insurance is insufficient.

---

### Recommendation

Add a guard before the division in `socializeSubaccount()`. If `openInterest == 0` and `vQuoteBalance < 0`, the loss cannot be socialized across other participants (there are none). The protocol should either absorb the residual via insurance, skip the product, or revert with a meaningful error rather than a panic:

```solidity
if (balance.vQuoteBalance < 0) {
    if (state.openInterest == 0) {
        // No participants to socialize against; absorb via insurance or skip
        balance.vQuoteBalance = 0;
    } else {
        int128 fundingPerShare = -balance.vQuoteBalance.div(state.openInterest);
        state.cumulativeFundingLongX18 += fundingPerShare;
        state.cumulativeFundingShortX18 -= fundingPerShare;
        balance.vQuoteBalance = 0;
    }
}
```

The exact handling policy (absorb into insurance, skip, or emit an event) should be decided based on protocol economics, but the division must be guarded unconditionally.

---

### Proof of Concept

**Setup:**
- One perp market, `productId = 2`, with a single participant: Alice (`subaccount = alice`).
- Alice opens a short position: `balance.amount = -100`, `vQuoteBalance = +10000` (she received quote when shorting).
- `state.openInterest = 100`.

**Step 1 — Alice's position is liquidated (prior steps):**
- Sequencer submits `LiquidateSubaccount` for Alice with `productId = 2`.
- `perpEngine.updateBalance(2, alice, +100, -10500)` closes the position at a loss.
- After update: `balance.amount = 0`, `balance.vQuoteBalance = -500`, `state.openInterest = 0`.

**Step 2 — Finalization attempt:**
- Sequencer submits `LiquidateSubaccount` for Alice with `productId = type(uint32).max`.
- `_finalizeSubaccount` checks `balance.amount == 0` ✓.
- Positive PnL loop: `vQuoteBalance = -500 < 0`, skipped.
- Negative PnL loop: `quoteBalance.amount = 0` (Alice has no quote), skipped.
- `v.canLiquidateMore = (0 + insurance) > 0` — assume insurance is 0 or insufficient.
- `perpEngine.socializeSubaccount(alice, 0)` is called.

**Step 3 — Revert:**
- Loop reaches `productId = 2`.
- `balance.vQuoteBalance = -500 < 0`.
- `insuranceCover = min(0, 500) = 0`.
- `balance.vQuoteBalance` remains `-500 < 0`.
- `fundingPerShare = -(-500).div(0)` → **division by zero → revert**.

**Result:** The finalization transaction reverts. Alice's subaccount can never be finalized. The `-500` bad debt is permanently stuck, unresolvable by any protocol participant. [6](#0-5) [7](#0-6)

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

**File:** core/contracts/ClearinghouseLiq.sol (L598-627)
```text
    function liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn)
        external
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
        require(
            txn.liquidatee != X_ACCOUNT && txn.liquidatee != N_ACCOUNT,
            ERR_NOT_LIQUIDATABLE
        );
        require(
            txn.productId != QUOTE_PRODUCT_ID,
            ERR_INVALID_LIQUIDATION_PARAMS
        );

        ISpotEngine spotEngine = ISpotEngine(
            address(engineByType[IProductEngine.EngineType.SPOT])
        );
        IPerpEngine perpEngine = IPerpEngine(
            address(engineByType[IProductEngine.EngineType.PERP])
        );

        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
            return;
        }
```
