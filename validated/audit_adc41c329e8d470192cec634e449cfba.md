### Title
Spread Health Adjustment Turns Negative for High-Weight Assets, Enabling False Liquidation — (`core/contracts/Clearinghouse.sol`)

---

### Summary

In `Clearinghouse.getHealth`, the spread health adjustment `basisAmount.mul(spotCoreRisk.price + perpCoreRisk.price).mul(spreadWeight - existingWeight)` can produce a **negative** value when both underlying products have `longWeight` at or near `ONE` (1e18). This is because `_getSpreadWeightX18` caps `spreadWeight` at `0.99e18` (INITIAL) or `0.994e18` (MAINTENANCE), while `existingWeight` can reach `1e18`. The result is that a fully hedged basis trader's health is understated, making them falsely liquidatable.

---

### Finding Description

**`existingWeight` computation** (Clearinghouse.sol lines 125–126):

```solidity
int128 existingWeight = (spotCoreRisk.longWeight + perpCoreRisk.longWeight) / 2;
```

`getCoreRisk` in `BaseEngine.sol` always passes `1` (positive) as the amount argument to `_getWeightX18`, so `longWeight` is always the product's configured long weight regardless of actual position direction. [1](#0-0) 

The maximum allowed `longWeightInitial` in `RiskStore` is `10**9` (enforced by `_addOrUpdateProduct`), which converts to `1e18 = ONE` in X18 format via `int128(s.longWeightInitial) * 1e9`. [2](#0-1) [3](#0-2) 

So `existingWeight` can legitimately reach `ONE = 1e18`.

**`spreadWeight` computation** (RiskHelper.sol lines 57–81):

For the standard basis trade (`spotCoreRisk.amount > 0`, long spot / short perp):

```solidity
spreadWeight = ONE - (ONE - perpCoreRisk.longWeight) / 5;
```

When `perpCoreRisk.longWeight = ONE`: `spreadWeight = ONE - 0 = ONE`.

Then the cap is applied:
```solidity
if (spreadWeight > maxSpreadWeight) {
    spreadWeight = maxSpreadWeight;  // 0.99e18 (INITIAL) or 0.994e18 (MAINTENANCE)
}
``` [4](#0-3) 

So `spreadWeight` is clamped to `0.99e18`, while `existingWeight = 1e18`.

**The broken invariant:**

```
spreadWeight - existingWeight = 0.99e18 - 1e18 = -0.01e18
```

The health adjustment becomes:
```solidity
health += basisAmount          // > 0 (long spot, short perp)
    .mul(spotCoreRisk.price + perpCoreRisk.price)  // > 0
    .mul(spreadWeight - existingWeight);            // < 0  ← NEGATIVE
``` [5](#0-4) 

The spread adjustment, which is supposed to **add** health (reward hedging), instead **subtracts** health.

---

### Impact Explanation

A basis trader holding long spot + short perp on any product with `longWeightInitial = 1e9` (the protocol maximum, used by the QUOTE product itself) will have their health understated by:

```
penalty = basisAmount * (spotPrice + perpPrice) * 0.01
```

For a 1 BTC position at $50,000 per leg: `1e18 * 100,000e18 * 0.01e18 / 1e36 = $1,000` of phantom health deficit per BTC. A liquidator can call the liquidation path on this solvent account and extract value from it.

---

### Likelihood Explanation

The QUOTE product itself is initialized with `longWeightInitial: 1e9` (= ONE). [6](#0-5) 

Any product configured at the protocol-allowed maximum weight triggers this. No special privileges or unusual conditions are required — it is a normal operating state for high-quality collateral assets.

---

### Recommendation

In `_getSpreadWeightX18`, ensure `spreadWeight` is always at least `existingWeight` before applying the cap, or restructure the cap logic so it never produces `spreadWeight < existingWeight`. One approach:

```solidity
// After computing spreadWeight, ensure it is >= existingWeight
int128 existingWeightLocal = (perpCoreRisk.longWeight + spotCoreRisk.longWeight) / 2;
if (spreadWeight < existingWeightLocal) {
    spreadWeight = existingWeightLocal;
}
```

Alternatively, apply the `maxSpreadWeight` cap only as an upper bound relative to `existingWeight`, not as an absolute floor that can undercut it.

---

### Proof of Concept

Concrete values using protocol-valid parameters:

| Parameter | Value |
|---|---|
| `spotCoreRisk.longWeight` | `1e18` (= `longWeightInitial: 1e9` in RiskStore) |
| `perpCoreRisk.longWeight` | `1e18` |
| `existingWeight` | `(1e18 + 1e18) / 2 = 1e18` |
| `spreadWeight` (pre-cap) | `1e18 - (1e18 - 1e18)/5 = 1e18` |
| `spreadWeight` (post-cap, INITIAL) | `0.99e18` |
| `spreadWeight - existingWeight` | **`-0.01e18`** |
| `basisAmount` (1 BTC long spot / short perp) | `1e18` |
| `spotPrice + perpPrice` | `100,000e18` |
| Health delta | **`-1,000e18` (i.e., -$1,000 phantom deficit)** |

The account is solvent but `getHealth` returns a value $1,000 lower than it should, making it eligible for liquidation. A liquidator can call the liquidation entrypoint, pass the health check (which uses this same `getHealth`), and extract value from the solvent account. [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/BaseEngine.sol (L55-58)
```text
        r.longWeightInitialX18 = int128(s.longWeightInitial) * 1e9;
        r.shortWeightInitialX18 = int128(s.shortWeightInitial) * 1e9;
        r.longWeightMaintenanceX18 = int128(s.longWeightMaintenance) * 1e9;
        r.shortWeightMaintenanceX18 = int128(s.shortWeightMaintenance) * 1e9;
```

**File:** core/contracts/BaseEngine.sol (L187-191)
```text
            IProductEngine.CoreRisk(
                amount,
                risk.priceX18,
                RiskHelper._getWeightX18(risk, 1, healthType)
            );
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

**File:** core/contracts/libraries/RiskHelper.sol (L66-80)
```text
        if (spotCoreRisk.amount > 0) {
            spreadWeight = ONE - (ONE - perpCoreRisk.longWeight) / 5;
        } else {
            spreadWeight = ONE - (ONE - spotCoreRisk.longWeight) / 5;
        }
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

**File:** core/contracts/Clearinghouse.sol (L125-135)
```text
            int128 existingWeight = (spotCoreRisk.longWeight +
                perpCoreRisk.longWeight) / 2;
            int128 spreadWeight = RiskHelper._getSpreadWeightX18(
                perpCoreRisk,
                spotCoreRisk,
                healthType
            );

            health += basisAmount
                .mul(spotCoreRisk.price + perpCoreRisk.price)
                .mul(spreadWeight - existingWeight);
```

**File:** core/contracts/SpotEngine.sol (L32-38)
```text
        _risk().value[QUOTE_PRODUCT_ID] = RiskHelper.RiskStore({
            longWeightInitial: 1e9,
            shortWeightInitial: 1e9,
            longWeightMaintenance: 1e9,
            shortWeightMaintenance: 1e9,
            priceX18: ONE
        });
```
