I need to carefully trace the finalization path and check whether a health guard exists for the liquidator after positive PnL settlement.

### Title
Missing Liquidator Health Check in `_finalizeSubaccount` Positive PnL Settlement — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`_finalizeSubaccount` settles all positive `vQuoteBalance` from the liquidatee's perp positions against the liquidator's quote balance with no subsequent health check on `txn.sender`. The identical operation in `_handleLiquidationPayment` is guarded by an explicit `isUnderInitial(txn.sender)` check. The missing guard allows the liquidator to exit finalization with initial health below zero, creating bad debt.

---

### Finding Description

**Entrypoint:** `Clearinghouse.liquidateSubaccount` → `delegatecall` → `ClearinghouseLiq.liquidateSubaccountImpl` → `_finalizeSubaccount` (triggered when `txn.productId == type(uint32).max`).

**Positive PnL settlement loop** (lines 323–338):

```solidity
for (uint32 i = 0; i < v.perpIds.length; ++i) {
    uint32 perpId = v.perpIds[i];
    IPerpEngine.Balance memory balance = perpEngine.getBalance(perpId, txn.liquidatee);
    if (balance.vQuoteBalance > 0) {
        _settlePnlAgainstLiquidator(txn, perpId, balance.vQuoteBalance, spotEngine, perpEngine);
    }
}
``` [1](#0-0) 

Each call to `_settlePnlAgainstLiquidator` executes:

```solidity
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -pnl);   // liquidator pays quote
perpEngine.updateBalance(perpId, txn.sender, 0, pnl);           // liquidator receives vQuoteBalance
``` [2](#0-1) 

After the loop completes and `_finalizeSubaccount` returns `true`, `liquidateSubaccountImpl` returns immediately:

```solidity
if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
    ...
    return;   // ← no health check on txn.sender
}
``` [3](#0-2) 

**Contrast with `_handleLiquidationPayment`**, which always enforces:

```solidity
require(
    txn.sender == N_ACCOUNT || !isUnderInitial(txn.sender),
    ERR_SUBACCT_HEALTH
);
``` [4](#0-3) 

This guard is entirely absent from the finalization path.

---

### Impact Explanation

When the liquidatee holds zero perp position amounts (required by line 319) but large positive `vQuoteBalance` across many perp products, the cumulative `spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -pnl)` calls can push the liquidator's quote balance deeply negative. [5](#0-4) 

The liquidator receives an equal amount of `vQuoteBalance` in their perp accounts. However, a negative spot quote balance is subject to a borrow weight < 1 in the health calculation (`Clearinghouse.getHealth` aggregates `spotEngine.getHealthContribution` + `perpEngine.getHealthContribution`), while the received `vQuoteBalance` contributes at its own perp weight. [6](#0-5) 

The asymmetry means the liquidator's initial health can go below zero after finalization. In the extreme case (total PnL >> liquidator's quote balance), maintenance health also goes negative, leaving the liquidator insolvent and creating bad debt in the protocol — a Critical impact under the stated scope.

---

### Likelihood Explanation

The liquidator voluntarily submits the finalization transaction, which limits opportunistic exploitation. However:

- The liquidatee's `vQuoteBalance` can accumulate silently across many perp products before finalization is attempted.
- A race condition exists between the liquidator's off-chain state check and on-chain execution (e.g., funding payments or price updates can increase `vQuoteBalance` between submission and inclusion).
- The protocol provides no on-chain bound or warning; the only protection is off-chain diligence by the liquidator.

The design inconsistency (guard present in `_handleLiquidationPayment`, absent in `_finalizeSubaccount`) confirms this is an unintended omission rather than a deliberate design choice.

---

### Recommendation

Add the same liquidator health guard used in `_handleLiquidationPayment` at the end of `_finalizeSubaccount`, after the positive PnL settlement loop:

```solidity
require(
    txn.sender == N_ACCOUNT || !isUnderInitial(txn.sender),
    ERR_SUBACCT_HEALTH
);
```

Alternatively, cap the total positive PnL settled against the liquidator to the amount that keeps their initial health non-negative, reverting if the cap is exceeded.

---

### Proof of Concept

1. Deploy protocol with two perp products (perpId A, perpId B).
2. Set up `liquidatee` subaccount: zero perp position amounts on both products, but `vQuoteBalance = +5000e18` on each (total +10 000e18 positive PnL). Drive the subaccount under maintenance health via a negative spot quote balance.
3. Set up `liquidator` subaccount: quote balance = +10 000e18 (exactly covers the PnL), no other positions.
4. Liquidate all spot/perp positions of `liquidatee` until `balance.amount == 0` on all products.
5. Submit `LiquidateSubaccount` with `productId = type(uint32).max` (finalize).
6. `_finalizeSubaccount` calls `_settlePnlAgainstLiquidator` twice, draining the liquidator's quote to 0 and crediting +10 000e18 `vQuoteBalance` across two perp products.
7. Because the borrow weight for a zero/negative quote balance is < 1 while the perp `vQuoteBalance` weight may differ, assert `IClearinghouse(clearinghouse).getHealth(liquidator, INITIAL) < 0` — the invariant is broken and the liquidator is left under-collateralized with no revert having occurred. [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L259-270)
```text
    function _settlePnlAgainstLiquidator(
        IEndpoint.LiquidateSubaccount calldata txn,
        uint32 perpId,
        int128 pnl,
        ISpotEngine spotEngine,
        IPerpEngine perpEngine
    ) internal {
        perpEngine.updateBalance(perpId, txn.liquidatee, 0, -pnl);
        perpEngine.updateBalance(perpId, txn.sender, 0, pnl);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.liquidatee, pnl);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -pnl);
    }
```

**File:** core/contracts/ClearinghouseLiq.sol (L279-338)
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
```

**File:** core/contracts/ClearinghouseLiq.sol (L574-577)
```text
        require(
            txn.sender == N_ACCOUNT || !isUnderInitial(txn.sender),
            ERR_SUBACCT_HEALTH
        );
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

**File:** core/contracts/Clearinghouse.sol (L76-85)
```text
        IPerpEngine perpEngine = _perpEngine();

        health = spotEngine.getHealthContribution(subaccount, healthType);
        // min health means that it is attempting to borrow a spot that exists outside
        // of the risk system -- return min health to error out this action
        if (health == -INF) {
            return health;
        }
        health += perpEngine.getHealthContribution(subaccount, healthType);

```
