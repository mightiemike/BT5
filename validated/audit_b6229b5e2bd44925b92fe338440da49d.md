### Title
`lastLiquidationFees` Withheld From Insurance During Finalization Causes Premature Loss Socialization to LP Depositors — (`core/contracts/ClearinghouseLiq.sol`)

---

### Summary

In `_finalizeSubaccount`, the protocol intentionally subtracts `lastLiquidationFees` from the insurance balance before using it to cover a liquidatee's outstanding debt. This means a portion of the insurance fund is never applied to cover losses before socialization is triggered, causing LP depositors to absorb losses that the insurance fund could have covered.

---

### Finding Description

In `_finalizeSubaccount`, the insurance available for covering the liquidatee's outstanding debt is computed as:

```solidity
v.insurance = insurance;
v.insurance -= lastLiquidationFees;
v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0;
``` [1](#0-0) 

`v.insurance` (not the full `insurance`) is then passed to `perpEngine.socializeSubaccount` and used to compute `insuranceCover` for the spot quote balance:

```solidity
v.insurance = perpEngine.socializeSubaccount(txn.liquidatee, v.insurance);
int128 insuranceCover = MathHelper.min(v.insurance, -quoteBalance.amount);
``` [2](#0-1) 

After socialization completes, `lastLiquidationFees` is added back and stored:

```solidity
v.insurance += lastLiquidationFees;
insurance = v.insurance;
``` [3](#0-2) 

The protocol comment at lines 581–586 of `_handleLiquidationPayment` explicitly acknowledges this design:

> "if insurance is not enough for making a subaccount healthy, we should use all insurance to buy its liabilities, then socialize the subaccount. however, after the first step, insurance funds will be refilled a little bit which blocks the second step, so we keep the fees of the last liquidation and do not use this part in socialization to unblock it." [4](#0-3) 

`lastLiquidationFees` is set to the fees collected from the most recent liquidation step:

```solidity
lastLiquidationFees = v.liquidationFees;
``` [5](#0-4) 

These fees are always positive (they are a fraction of the oracle-to-liquidation-price spread multiplied by the liquidated amount):

```solidity
v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
    .mul(LIQUIDATION_FEE_FRACTION)
    .mul(txn.amount);
``` [6](#0-5) 

The consequence is structural: when a subaccount has outstanding debt at finalization time, the insurance fund covers only `insurance - lastLiquidationFees` of that debt. The remaining `lastLiquidationFees` worth of coverage is withheld and preserved in the insurance fund, while LP depositors absorb the corresponding loss through socialization.

The `canLiquidateMore` flag is also affected: by subtracting `lastLiquidationFees`, the threshold for triggering socialization is lowered. When `v.canLiquidateMore = false`, the check requiring all spot liabilities to be zero (line 382) is skipped, allowing finalization with outstanding spot liabilities that are then socialized. [7](#0-6) 

---

### Impact Explanation

LP depositors (holders of NLP tokens, whose value is backed by `cumulativeDepositsMultiplierX18` in `SpotEngine`) bear losses equal to `lastLiquidationFees` per finalization event that could have been covered by the insurance fund. In `SpotEngine.socializeSubaccount`, losses are socialized by reducing `cumulativeDepositsMultiplierX18`, directly diluting all depositors' balances:

```solidity
state.cumulativeDepositsMultiplierX18 = (totalDeposited + balance.amount)
    .div(state.totalDepositsNormalized);
``` [8](#0-7) 

For large liquidations, `lastLiquidationFees` can be a non-trivial amount. The insurance fund retains these fees while LP depositors absorb the equivalent loss — a direct analog to the external report's finding where the reserve price is not enforced, causing LP funds to be lost when the liquidator receives collateral without covering the full outstanding debt.

---

### Likelihood Explanation

This triggers on every finalization of an insolvent subaccount where the insurance fund is insufficient to cover all outstanding debt. This is the normal path for any deeply insolvent account. The liquidator (any unprivileged caller) submits a `LiquidateSubaccount` transaction with `productId = type(uint32).max` to trigger `_finalizeSubaccount`. No special privileges are required. [9](#0-8) 

---

### Recommendation

Remove the `lastLiquidationFees` subtraction from the insurance calculation in `_finalizeSubaccount`, or use the full `insurance` balance when computing `insuranceCover` and passing to `perpEngine.socializeSubaccount`. If the "unblocking" behavior is required, find an alternative mechanism that does not withhold insurance from covering outstanding debt — for example, by restructuring the `canLiquidateMore` check to not depend on `lastLiquidationFees`.

---

### Proof of Concept

1. Subaccount S has outstanding debt of 1000 USDC after all regular liquidation steps complete.
2. `insurance = 50`, `lastLiquidationFees = 50` (the last liquidation step added exactly 50 to insurance).
3. `_finalizeSubaccount` is called with `txn.productId = type(uint32).max`.
4. `v.insurance = 50 - 50 = 0`.
5. `perpEngine.socializeSubaccount(S, 0)` is called — zero insurance is applied to perp losses; they are fully socialized.
6. `insuranceCover = min(0, 1000) = 0` — zero insurance covers the spot quote deficit.
7. `spotEngine.socializeSubaccount(S)` is called — the full 1000 USDC loss is socialized to LP depositors, reducing `cumulativeDepositsMultiplierX18`.
8. `v.insurance += 50; insurance = 50` — the insurance fund retains 50 USDC.

Result: LP depositors absorb 50 USDC of losses that the insurance fund held and could have covered. The insurance fund is preserved at the expense of LP depositors, mirroring the external report's finding where the reserve price is not enforced and LP funds are lost.

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L368-370)
```text
        v.insurance = insurance;
        v.insurance -= lastLiquidationFees;
        v.canLiquidateMore = (quoteBalance.amount + v.insurance) > 0;
```

**File:** core/contracts/ClearinghouseLiq.sol (L372-384)
```text
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
```

**File:** core/contracts/ClearinghouseLiq.sol (L386-398)
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
```

**File:** core/contracts/ClearinghouseLiq.sol (L410-411)
```text
        v.insurance += lastLiquidationFees;
        insurance = v.insurance;
```

**File:** core/contracts/ClearinghouseLiq.sol (L550-552)
```text
            v.liquidationFees = (v.oraclePriceX18 - v.liquidationPriceX18)
                .mul(LIQUIDATION_FEE_FRACTION)
                .mul(txn.amount);
```

**File:** core/contracts/ClearinghouseLiq.sol (L581-586)
```text
        // if insurance is not enough for making a subaccount healthy, we should
        // use all insurance to buy its liabilities, then socialize the subaccount
        // however, after the first step, insurance funds will be refilled a little bit
        // which blocks the second step, so we keep the fees of the last liquidation and
        // do not use this part in socialization to unblock it.
        lastLiquidationFees = v.liquidationFees;
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

**File:** core/contracts/SpotEngine.sol (L260-261)
```text
                state.cumulativeDepositsMultiplierX18 = (totalDeposited +
                    balance.amount).div(state.totalDepositsNormalized);
```
