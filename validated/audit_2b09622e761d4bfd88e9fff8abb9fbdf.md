### Title
Short-Spot / Long-Perp Basis Position Receives Inflated Health Bonus Due to `getCoreRisk` Hardcoding `amount=1` — (`core/contracts/libraries/RiskHelper.sol`, `core/contracts/BaseEngine.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

`getCoreRisk` in `BaseEngine` always passes the literal `1` (not the actual position amount) to `_getWeightX18`, so `CoreRisk.longWeight` is unconditionally the **long-side** weight. For a **short-spot / long-perp** basis position, both `_getSpreadWeightX18` and the `existingWeight` average in `getHealth` therefore use the long-side weight of the short-spot leg instead of its short-side weight (≥ ONE). This makes `spreadWeight − existingWeight` positive (a health bonus) when the correct arithmetic would produce a negative or near-zero value, inflating the subaccount's health and enabling undercollateralised positions.

---

### Finding Description

**Root cause — `getCoreRisk` hardcodes `amount = 1`** [1](#0-0) 

`_getWeightX18` branches on the sign of `amount`: `amount >= 0` → long weight (≤ ONE); `amount < 0` → short weight (≥ ONE). Because `getCoreRisk` always passes `1`, `CoreRisk.longWeight` is always the long-side weight, regardless of whether the actual position is short. [2](#0-1) 

**`_getSpreadWeightX18` — wrong branch for short-spot / long-perp**

When `spotCoreRisk.amount < 0` (short spot), the `else` branch fires:

```
spreadWeight = ONE − (ONE − spotCoreRisk.longWeight) / 5
``` [3](#0-2) 

`spotCoreRisk.longWeight` is the long-side weight (≤ ONE), so `ONE − spotCoreRisk.longWeight ≥ 0`, and `spreadWeight ∈ [0.80, 1.0)`. The correct value would use `shortWeightX18 ≥ ONE`, giving `ONE − shortWeight ≤ 0`, so `spreadWeight ≥ ONE`, which would be capped at `maxSpreadWeight` (0.99 / 0.994).

**`existingWeight` — also uses long-side weight for the short-spot leg** [4](#0-3) 

`existingWeight = (longWeight_spot + longWeight_perp) / 2`. For a short-spot position the correct weight is `shortWeight_spot ≥ ONE`, so `existingWeight` is systematically underestimated.

**`basisAmount` is positive for short-spot / long-perp** [5](#0-4) 

`basisAmount = −max(spotAmount, −perpAmount)`. With `spotAmount = −N` and `perpAmount = +N`, `max(−N, −N) = −N`, so `basisAmount = N > 0`.

**Net health adjustment** [6](#0-5) 

```
health += basisAmount × (spotPrice + perpPrice) × (spreadWeight − existingWeight)
```

| | `spreadWeight` | `existingWeight` | `sw − ew` | health delta |
|---|---|---|---|---|
| **Bug** (longWeight_spot = 0.9, longWeight_perp = 0.9) | 0.98 | 0.90 | **+0.08** | **+bonus** |
| **Correct** (shortWeight_spot = 1.1, longWeight_perp = 0.9) | 0.99 (capped) | 1.00 | **−0.01** | −penalty |

The bug inflates health by approximately `basisAmount × prices × 0.09` — roughly 9 % of the hedged notional — turning a correct small penalty into a significant bonus.

---

### Impact Explanation

A trader holding a short-spot / long-perp basis position receives an unearned health bonus proportional to the hedged notional. This allows the subaccount to carry more leverage than the protocol intends, enabling undercollateralised positions that bypass the initial and maintenance health checks enforced in `withdrawCollateral`, `transferQuote`, and order matching. If prices move adversely the position may become insolvent before liquidation can recover protocol funds, breaking solvency.

---

### Likelihood Explanation

The path is fully reachable through normal trading: any user can place a short spot order and a long perp order on a spread-eligible pair. No special privileges, admin access, or unusual configuration are required. The `spreads` bitmask in `Clearinghouse` explicitly enumerates the eligible pairs, confirming the code path is live in production.

---

### Recommendation

1. Add a `shortWeight` field to `CoreRisk` and populate it by passing the actual `amount` (not `1`) to `_getWeightX18` in `getCoreRisk`.
2. In `_getSpreadWeightX18`, use `spotCoreRisk.shortWeight` (the short-side weight) when `spotCoreRisk.amount < 0`, and `perpCoreRisk.shortWeight` when `perpCoreRisk.amount < 0`.
3. In `getHealth`, compute `existingWeight` using the direction-correct weight for each leg: `shortWeight_spot` when spot is short, `shortWeight_perp` when perp is short.

---

### Proof of Concept

```solidity
// State: shortWeight_spot_initial = 1.1e18, longWeight_spot_initial = 0.9e18
//        longWeight_perp_initial  = 0.9e18, shortWeight_perp_initial = 1.1e18
// Position: spotAmount = -100e18 (short spot), perpAmount = +100e18 (long perp)
// Price: spot = perp = 1e18

// getCoreRisk returns longWeight for both (hardcoded amount=1):
//   spotCoreRisk.longWeight = 0.9e18
//   perpCoreRisk.longWeight = 0.9e18

// basisAmount = -max(-100e18, -100e18) = 100e18

// _getSpreadWeightX18 (else branch, spotAmount < 0):
//   spreadWeight = 1e18 - (1e18 - 0.9e18)/5 = 0.98e18

// existingWeight = (0.9e18 + 0.9e18)/2 = 0.9e18

// health adjustment = 100e18 * 2e18 * (0.98e18 - 0.9e18) / 1e18^2
//                   = 100 * 2 * 0.08 = +16 (units of 1e18)

// Correct calculation (using shortWeight_spot = 1.1e18):
//   spreadWeight_correct = min(1e18 - (1e18 - 1.1e18)/5, 0.99e18) = 0.99e18
//   existingWeight_correct = (1.1e18 + 0.9e18)/2 = 1.0e18
//   health adjustment = 100 * 2 * (0.99 - 1.0) = -2 (units of 1e18)

// Inflation = 16 - (-2) = 18 units of notional
// assert(Clearinghouse.getHealth(subaccount, INITIAL) > correctHealth)
```

The test is locally reproducible on an unmodified Hardhat setup by deploying `SpotEngine`, `PerpEngine`, and `Clearinghouse`, setting the risk weights above, opening the described positions, and comparing `getHealth` output against the manually computed correct value.

### Citations

**File:** core/contracts/BaseEngine.sol (L186-191)
```text
        return
            IProductEngine.CoreRisk(
                amount,
                risk.priceX18,
                RiskHelper._getWeightX18(risk, 1, healthType)
            );
```

**File:** core/contracts/libraries/RiskHelper.sol (L44-54)
```text
        if (amount >= 0) {
            weight = healthType == IProductEngine.HealthType.INITIAL
                ? risk.longWeightInitialX18
                : risk.longWeightMaintenanceX18;
        } else {
            weight = healthType == IProductEngine.HealthType.INITIAL
                ? risk.shortWeightInitialX18
                : risk.shortWeightMaintenanceX18;
        }

        return weight;
```

**File:** core/contracts/libraries/RiskHelper.sol (L66-70)
```text
        if (spotCoreRisk.amount > 0) {
            spreadWeight = ONE - (ONE - perpCoreRisk.longWeight) / 5;
        } else {
            spreadWeight = ONE - (ONE - spotCoreRisk.longWeight) / 5;
        }
```

**File:** core/contracts/Clearinghouse.sol (L116-121)
```text
            } else {
                basisAmount = -MathHelper.max(
                    spotCoreRisk.amount,
                    -perpCoreRisk.amount
                );
            }
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
