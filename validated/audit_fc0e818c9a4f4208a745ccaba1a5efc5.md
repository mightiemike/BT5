### Title
`_settlePositivePerpPnl` Uses `getPositionPnl` Instead of `vQuoteBalance`, Causing Incorrect Settlement Amounts — (File: `core/contracts/ClearinghouseLiq.sol`)

---

### Summary

In `ClearinghouseLiq._settlePositivePerpPnl`, the function calls `perpEngine.getPositionPnl()` — which returns the full mark-to-market value `price × amount + vQuoteBalance` — instead of reading `balance.vQuoteBalance` directly. This is the exact analog of the reported bug: a wrong function is called that returns a computed/estimated value rather than the actual stored balance, corrupting the settlement amount during spot/spread liquidations.

---

### Finding Description

`_settlePositivePerpPnl` is invoked inside `liquidateSubaccountImpl` before a spot or spread liability liquidation, with the stated purpose of settling positive perp PnL into the liquidatee's spot quote balance so they have enough quote to buy back their spot liabilities. [1](#0-0) 

The function calls:

```solidity
int128 positionPnl = perpEngine.getPositionPnl(productId, txn.liquidatee);
if (positionPnl > 0) {
    _settlePnlAgainstLiquidator(txn, productId, positionPnl, ...);
}
```

`getPositionPnl` is defined as:

```solidity
positionPnl = priceX18.mul(balance.amount) + balance.vQuoteBalance;
``` [2](#0-1) 

This is **not** the accumulated PnL balance. It is the full mark-to-market value of the open position. The position (`balance.amount`) is **not closed** by `_settlePnlAgainstLiquidator`, which only adjusts `vQuoteBalance` and the spot quote balance: [3](#0-2) 

By contrast, `_finalizeSubaccount` — which handles the same PnL settlement in a different liquidation path — correctly uses `balance.vQuoteBalance` directly, and only does so after confirming `balance.amount == 0` (position already closed): [4](#0-3) 

The two functions are inconsistent. `_settlePositivePerpPnl` uses the wrong data source.

**Concrete accounting corruption:**

Consider a liquidatee with:
- Spot balance: `-100 USDC`
- Perp: `amount = 10`, `vQuoteBalance = -5`, oracle price = `2`
- `positionPnl = 2×10 + (−5) = 15`
- `vQuoteBalance = −5` (negative — no accumulated PnL to settle)

With the current code (`getPositionPnl`):
- Settlement amount = `15`
- Liquidatee receives `+15 USDC` in spot quote → spot balance becomes `−85`
- Liquidatee's perp `vQuoteBalance` becomes `−5 − 15 = −20`
- Liquidator pays `15 USDC` and receives nothing (position still held by liquidatee)
- New `positionPnl = 2×10 + (−20) = 0` — the position PnL is zeroed out while the position remains open

With the correct code (`vQuoteBalance`):
- `vQuoteBalance = −5 < 0` → condition `positionPnl > 0` is false → no settlement occurs
- No accounting distortion

---

### Impact Explanation

1. **Liquidator overpays**: The liquidator transfers `price × amount` extra USDC to the liquidatee for an open position that is not being closed. This is a direct financial loss to the liquidator.
2. **Liquidatee's spot liability is artificially reduced**: The extra spot quote received (`+15` in the example) reduces the liquidatee's spot liability, potentially allowing them to pass `_assertCanLiquidateLiability` with a smaller insurance contribution or avoid full liquidation.
3. **Perp accounting is corrupted**: The liquidatee's perp `vQuoteBalance` is driven deeply negative (`−price × amount`) while the position remains open. This distorts future funding, health, and settlement calculations for that position.
4. **Double-counting**: The liquidatee has been credited the full mark-to-market value of their open position in spot quote while still holding the position — a clear solvency accounting error.

---

### Likelihood Explanation

This is triggered on every spot or spread liquidation where the liquidatee holds an open perp position with `positionPnl > 0` but `vQuoteBalance ≤ 0` (i.e., unrealized gains exceed accumulated losses). This is a common state in volatile markets. Any liquidator calling `liquidateSubaccountImpl` via the `Endpoint` is an unprivileged, externally reachable entry point that triggers this path. [5](#0-4) 

---

### Recommendation

Replace `perpEngine.getPositionPnl(productId, txn.liquidatee)` with a direct read of `balance.vQuoteBalance` in `_settlePositivePerpPnl`, consistent with `_finalizeSubaccount`:

```solidity
// Before (incorrect):
int128 positionPnl = perpEngine.getPositionPnl(productId, txn.liquidatee);
if (positionPnl > 0) {
    _settlePnlAgainstLiquidator(txn, productId, positionPnl, ...);
}

// After (correct):
IPerpEngine.Balance memory balance = perpEngine.getBalance(productId, txn.liquidatee);
if (balance.vQuoteBalance > 0) {
    _settlePnlAgainstLiquidator(txn, productId, balance.vQuoteBalance, ...);
}
```

---

### Proof of Concept

1. Liquidatee holds: spot balance `−100 USDC`, perp `amount = 10`, `vQuoteBalance = −5`, oracle price `= 2`.
2. Liquidator calls `liquidateSubaccountImpl` with `txn.amount < 0` (spot liability liquidation).
3. `_settlePositivePerpPnl` computes `positionPnl = 2×10 + (−5) = 15 > 0` and calls `_settlePnlAgainstLiquidator` with `pnl = 15`.
4. `_settlePnlAgainstLiquidator` executes:
   - `perpEngine.updateBalance(perpId, liquidatee, 0, −15)` → `vQuoteBalance = −20`
   - `spotEngine.updateBalance(QUOTE_PRODUCT_ID, liquidatee, +15)` → spot balance = `−85`
   - `spotEngine.updateBalance(QUOTE_PRODUCT_ID, liquidator, −15)` → liquidator loses 15 USDC
5. The perp position (`amount = 10`) is **not closed**. The liquidatee still holds it.
6. Liquidatee's spot liability is now `−85` instead of `−100`, distorting the subsequent `_assertLiquidationAmount` bounds.
7. Liquidatee's perp `vQuoteBalance = −20` corrupts future health, funding, and settlement calculations. [1](#0-0) [6](#0-5) [3](#0-2)

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

**File:** core/contracts/ClearinghouseLiq.sol (L322-338)
```text
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

**File:** core/contracts/ClearinghouseLiq.sol (L415-437)
```text
    function _settlePositivePerpPnl(
        IEndpoint.LiquidateSubaccount calldata txn,
        ISpotEngine spotEngine,
        IPerpEngine perpEngine
    ) internal {
        uint32[] memory productIds = perpEngine.getProductIds();
        for (uint32 i = 0; i < productIds.length; ++i) {
            uint32 productId = productIds[i];
            int128 positionPnl = perpEngine.getPositionPnl(
                productId,
                txn.liquidatee
            );
            if (positionPnl > 0) {
                _settlePnlAgainstLiquidator(
                    txn,
                    productId,
                    positionPnl,
                    spotEngine,
                    perpEngine
                );
            }
        }
    }
```

**File:** core/contracts/ClearinghouseLiq.sol (L629-638)
```text
        if (
            (txn.amount < 0) &&
            (txn.isEncodedSpread ||
                address(productToEngine[txn.productId]) == address(spotEngine))
        ) {
            // when it's spread or spot liquidation, we need to make sure the liquidatee has
            // enough quote to buyback the liquidated amount.
            _assertCanLiquidateLiability(txn, spotEngine, perpEngine);
            _settlePositivePerpPnl(txn, spotEngine, perpEngine);
        }
```

**File:** core/contracts/PerpEngine.sol (L107-123)
```text
    function calculatePositionPnl(Balance memory balance, uint32 productId)
        internal
        returns (int128 positionPnl)
    {
        int128 priceX18 = _risk(productId).priceX18;
        positionPnl = priceX18.mul(balance.amount) + balance.vQuoteBalance;
        emit PriceQuery(productId);
    }

    function getPositionPnl(uint32 productId, bytes32 subaccount)
        external
        returns (int128)
    {
        (, Balance memory balance) = getStateAndBalance(productId, subaccount);

        return calculatePositionPnl(balance, productId);
    }
```
