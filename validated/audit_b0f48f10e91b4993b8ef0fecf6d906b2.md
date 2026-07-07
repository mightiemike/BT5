Now I have all the code needed for a complete analysis. Let me work through the math precisely.

### Title
Spread Health Contribution Turns Negative When Underlying `longWeight` Exceeds `maxSpreadWeight` Cap — (`core/contracts/Clearinghouse.sol`, `core/contracts/libraries/RiskHelper.sol`)

---

### Summary

The `maxSpreadWeight` cap in `_getSpreadWeightX18` can produce a `spreadWeight` that is **strictly less than** `existingWeight` when both underlying products have `longWeightX18 > maxSpreadWeight`. This causes the spread health adjustment in `getHealth` to be **negative**, making the subaccount appear unhealthier than it actually is and enabling premature liquidation.

---

### Finding Description

In `getHealth`, the spread loop computes a health adjustment:

```solidity
int128 existingWeight = (spotCoreRisk.longWeight + perpCoreRisk.longWeight) / 2;
int128 spreadWeight = RiskHelper._getSpreadWeightX18(perpCoreRisk, spotCoreRisk, healthType);
health += basisAmount.mul(spotCoreRisk.price + perpCoreRisk.price).mul(spreadWeight - existingWeight);
``` [1](#0-0) 

The design intent (per the comment on line 123) is that spread positions receive **higher leverage** than unhedged positions, so `spreadWeight - existingWeight` must always be ≥ 0.

`_getSpreadWeightX18` computes (for `spotCoreRisk.amount > 0`):

```solidity
spreadWeight = ONE - (ONE - perpCoreRisk.longWeight) / 5;
// then capped:
if (spreadWeight > maxSpreadWeight) spreadWeight = maxSpreadWeight;
``` [2](#0-1) 

Where `maxSpreadWeight = 0.99` (INITIAL) or `0.994` (MAINTENANCE). [3](#0-2) 

**Without the cap**, `spreadWeight_uncapped ≥ existingWeight` always holds for weights in [0, 1]:

```
1 - (1-p)/5 ≥ (s+p)/2
⟺ 8 ≥ 5s + 3p   [always true when s,p ≤ 1]
```

**With the cap**, if `existingWeight = (s+p)/2 > maxSpreadWeight`, then after capping `spreadWeight = maxSpreadWeight < existingWeight`, making `spreadWeight - existingWeight < 0`.

This requires `s + p > 2 × maxSpreadWeight`:
- INITIAL: `s + p > 1.98` (both weights > 0.99)
- MAINTENANCE: `s + p > 1.988` (both weights > 0.994)

`getCoreRisk` always passes `amount = 1` to `_getWeightX18`, so `CoreRisk.longWeight` is always the `longWeightInitialX18` / `longWeightMaintenanceX18` regardless of actual position direction: [4](#0-3) 

The `RiskStore` comment explicitly states weights are "between 0 and 2", so values above 0.99 are valid and expected for low-risk assets (e.g., stablecoin-backed products). [5](#0-4) 

---

### Impact Explanation

When `spreadWeight - existingWeight < 0`:

- `basisAmount > 0` (it is the min of the two offsetting position sizes, always positive by construction)
- `spotCoreRisk.price + perpCoreRisk.price > 0`
- The product is **negative**, so `health` **decreases** due to the spread

A subaccount holding a spread position on two high-weight products will have a **lower** computed health than if it held the same positions without the spread adjustment. This directly violates the protocol invariant and can cause the subaccount to fall below the liquidation threshold when it is actually solvent, enabling a liquidator to extract value from it.

---

### Likelihood Explanation

The condition requires both `longWeightInitialX18 > 0.99` (or `> 0.994` for maintenance). This is a realistic configuration for stablecoin-backed or very low-risk products. No attacker action is required to trigger it — any subaccount that opens a spread position on such a product pair will be affected automatically. The liquidation path is a standard, externally reachable protocol flow.

---

### Recommendation

Remove the `maxSpreadWeight` cap, or — if the cap is needed to bound leverage — ensure it is also applied to `existingWeight` so that `spreadWeight - existingWeight` is always ≥ 0:

```solidity
int128 existingWeight = (spotCoreRisk.longWeight + perpCoreRisk.longWeight) / 2;
if (existingWeight > maxSpreadWeight) existingWeight = maxSpreadWeight;
```

Alternatively, clamp the final delta: `spreadWeight - existingWeight` should be floored at 0 if the invariant is that spread positions must never penalize health.

---

### Proof of Concept

**Setup:** Configure a spread pair where both spot and perp have `longWeightInitialX18 = 0.995e18`.

**Computation (INITIAL health):**
- `existingWeight = (0.995e18 + 0.995e18) / 2 = 0.995e18`
- `spreadWeight_uncapped = 1e18 - (1e18 - 0.995e18) / 5 = 1e18 - 0.001e18 = 0.999e18`
- After cap: `spreadWeight = 0.99e18`
- `spreadWeight - existingWeight = 0.99e18 - 0.995e18 = -0.005e18`
- With `basisAmount = 1e18` and `price_sum = 2e18`:
  - `health delta = 1e18 * 2e18 * (-0.005e18) / 1e36 = -0.01e18` (negative)

**Assert:** `getHealth(subaccount, INITIAL)` with the spread position is **less than** `getHealth` without the spread adjustment — directly contradicting the comment on line 123 and the protocol's leverage invariant. [6](#0-5)

### Citations

**File:** core/contracts/Clearinghouse.sol (L123-135)
```text
            // spreads have 5x higher leverage than the underlying products.
            // but it's capped at 100x leverage at most.
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

**File:** core/contracts/libraries/RiskHelper.sol (L14-24)
```text
    struct RiskStore {
        // these weights are all
        // between 0 and 2
        // these integers are the real
        // weights times 1e9
        int32 longWeightInitial;
        int32 shortWeightInitial;
        int32 longWeightMaintenance;
        int32 shortWeightMaintenance;
        int128 priceX18;
    }
```

**File:** core/contracts/libraries/RiskHelper.sol (L66-79)
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
```

**File:** core/contracts/BaseEngine.sol (L186-191)
```text
        return
            IProductEngine.CoreRisk(
                amount,
                risk.priceX18,
                RiskHelper._getWeightX18(risk, 1, healthType)
            );
```
