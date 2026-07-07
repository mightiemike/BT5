### Title
Spread Health Adjustment Inverts to Penalty When `existingWeight > maxSpreadWeight`, Enabling Erroneous Liquidation — (`core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.getHealth` applies a spread health bonus via `basisAmount.mul(spotCoreRisk.price + perpCoreRisk.price).mul(spreadWeight - existingWeight)`. When both underlying products have long weights at or near `1.0`, the `maxSpreadWeight` cap in `_getSpreadWeightX18` forces `spreadWeight` below `existingWeight`, making the term negative. The spread position then **reduces** health instead of increasing it, violating the core invariant and potentially triggering an erroneous liquidation.

---

### Finding Description

**Step 1 — Weight storage and conversion**

Weights are stored as `int32` scaled by `1e9` and converted to X18 by multiplying by `1e9` again: [1](#0-0) 

The validation constraint enforces `longWeightMaintenance ≤ 10^9`, so `longWeightMaintenanceX18 ≤ ONE` and `longWeightInitialX18 ≤ ONE`: [2](#0-1) 

**Step 2 — `getCoreRisk` always returns the long weight**

`getCoreRisk` calls `_getWeightX18(risk, 1, healthType)` — hardcoded `amount = 1` — so `CoreRisk.longWeight` is always the long-side weight regardless of the actual position direction: [3](#0-2) 

**Step 3 — `existingWeight` can equal `ONE`**

`existingWeight` is the average of both products' long weights: [4](#0-3) 

If both products have `longWeightInitial = 10^9` (the maximum allowed), then `existingWeight = (ONE + ONE) / 2 = ONE`.

**Step 4 — `spreadWeight` is hard-capped below `ONE`**

`_getSpreadWeightX18` caps `spreadWeight` at `0.99 * ONE` (INITIAL) or `0.994 * ONE` (MAINTENANCE): [5](#0-4) 

When both underlying weights equal `ONE`, the uncapped formula gives `ONE - (ONE - ONE)/5 = ONE`, which is then clamped to `0.99 * ONE`. So `spreadWeight = 0.99 * ONE < existingWeight = ONE`.

**Step 5 — The health delta becomes negative** [6](#0-5) 

With `basisAmount > 0`, `prices > 0`, and `spreadWeight - existingWeight = -0.01 * ONE`, the entire term is negative. The spread position subtracts from health rather than adding to it.

---

### Impact Explanation

A subaccount holding a spread position in high-weight assets (e.g., stablecoin spot + stablecoin perp, both with `longWeightInitial = 10^9`) will have its `getHealth` return a value **lower** than the sum of the individual product health contributions. If the artificial health deficit pushes the account below the liquidation threshold, a liquidator can seize assets at a discount, transferring value from the subaccount to the liquidator. This is a direct solvency/accounting failure in the health check path.

---

### Likelihood Explanation

The condition is reachable under normal protocol configuration. Any product pair where both `longWeightInitialX18 > 0.99 * ONE` (i.e., `longWeightInitial > 990_000_000` in stored units) will trigger the inversion. High-quality collateral assets (e.g., USDC, USDT) are routinely configured with weights close to `1.0`. No special privileges or external compromise are required — the attacker simply needs to hold a spread position in such a product pair and wait for the health check.

---

### Recommendation

Remove or redesign the `maxSpreadWeight` cap so that it never produces `spreadWeight < existingWeight`. One approach: after computing `spreadWeight`, clamp it from below as well:

```solidity
if (spreadWeight < existingWeight) {
    spreadWeight = existingWeight;
}
```

Alternatively, restructure the spread bonus to be additive only (i.e., `max(spreadWeight, existingWeight) - existingWeight` is always ≥ 0).

---

### Proof of Concept

Configure two products with `longWeightInitial = longWeightMaintenance = 10^9` (= `ONE` in X18). Open a spread: long 1 unit of spot, short 1 unit of perp.

```
existingWeight = (ONE + ONE) / 2 = ONE
spreadWeight   = min(ONE - (ONE - ONE)/5, 0.99*ONE) = 0.99*ONE
delta          = spreadWeight - existingWeight = -0.01*ONE

healthAdjustment = basisAmount * (spotPrice + perpPrice) * (-0.01*ONE)
                 = 1 * (spotPrice + perpPrice) * (-0.01)   [negative]
```

Call `getHealth(subaccount, INITIAL)` and assert the returned value is **less than** the sum of `spotEngine.getHealthContribution` + `perpEngine.getHealthContribution`. The assertion holds, confirming the spread penalty. If the subaccount's margin is thin, this deficit crosses the liquidation threshold and `liquidateSubaccount` can be called, transferring collateral to the liquidator at a discount.

### Citations

**File:** core/contracts/BaseEngine.sol (L54-59)
```text
        RiskHelper.RiskStore memory s = _risk().value[productId];
        r.longWeightInitialX18 = int128(s.longWeightInitial) * 1e9;
        r.shortWeightInitialX18 = int128(s.shortWeightInitial) * 1e9;
        r.longWeightMaintenanceX18 = int128(s.longWeightMaintenance) * 1e9;
        r.shortWeightMaintenanceX18 = int128(s.shortWeightMaintenance) * 1e9;
        r.priceX18 = s.priceX18;
```

**File:** core/contracts/BaseEngine.sol (L184-192)
```text
        RiskHelper.Risk memory risk = _risk(productId);
        (int128 amount, ) = _getBalance(productId, subaccount);
        return
            IProductEngine.CoreRisk(
                amount,
                risk.priceX18,
                RiskHelper._getWeightX18(risk, 1, healthType)
            );
    }
```

**File:** core/contracts/BaseEngine.sol (L235-242)
```text
        require(
            riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
                riskStore.longWeightMaintenance <= 10**9 &&
                riskStore.shortWeightInitial >=
                riskStore.shortWeightMaintenance &&
                riskStore.shortWeightMaintenance >= 10**9,
            ERR_BAD_PRODUCT_CONFIG
        );
```

**File:** core/contracts/Clearinghouse.sol (L125-126)
```text
            int128 existingWeight = (spotCoreRisk.longWeight +
                perpCoreRisk.longWeight) / 2;
```

**File:** core/contracts/Clearinghouse.sol (L133-135)
```text
            health += basisAmount
                .mul(spotCoreRisk.price + perpCoreRisk.price)
                .mul(spreadWeight - existingWeight);
```

**File:** core/contracts/libraries/RiskHelper.sol (L71-80)
```text
        int128 maxSpreadWeight;
        if (healthType == IProductEngine.HealthType.INITIAL) {
            maxSpreadWeight = ONE - ONE / 100; // 0.99
        } else {
            maxSpreadWeight = ONE - (ONE * 6) / 1000; // 0.994
        }
        if (spreadWeight > maxSpreadWeight) {
            spreadWeight = maxSpreadWeight;
        }
        return spreadWeight;
```
