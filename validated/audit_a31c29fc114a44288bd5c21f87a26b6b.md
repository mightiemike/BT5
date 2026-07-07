The critical code is now fully in view. Let me trace the exact bug.

**`BaseEngine.getCoreRisk` always passes hardcoded `1` to `_getWeightX18`:** [1](#0-0) 

This means `CoreRisk.longWeight` is **always** the long weight, regardless of the actual position direction (the field name is accurate — it is always the long weight).

**`Clearinghouse.getHealth` then uses `longWeight` from both sides to compute `existingWeight`:** [2](#0-1) 

**`_addOrUpdateProduct` enforces the weight invariant:** [3](#0-2) 

This confirms: `longWeightInitial ≤ 1e9` (≤ 1.0) and `shortWeightInitial ≥ 1e9` (≥ 1.0).

---

### Title
`getCoreRisk` hardcodes long weight regardless of position direction, causing `existingWeight` underestimation for short-spot/long-perp spreads — (`core/contracts/Clearinghouse.sol`, `core/contracts/BaseEngine.sol`)

### Summary

`BaseEngine.getCoreRisk` always passes the literal `1` (positive) to `RiskHelper._getWeightX18`, so `CoreRisk.longWeight` is always the long-side weight. In `Clearinghouse.getHealth`, `existingWeight` is computed as `(spotCoreRisk.longWeight + perpCoreRisk.longWeight) / 2`. For a short-spot/long-perp spread, the spot position's actual health contribution used `shortWeightInitial` (≥ 1.0), but `existingWeight` uses `longWeightInitial` (≤ 1.0) for the spot side. This underestimates `existingWeight`, inflates `(spreadWeight - existingWeight)`, and grants an excess health bonus.

### Finding Description

In `BaseEngine.getCoreRisk`:

```solidity
return IProductEngine.CoreRisk(
    amount,
    risk.priceX18,
    RiskHelper._getWeightX18(risk, 1, healthType)  // hardcoded 1, always long weight
);
``` [4](#0-3) 

`_getWeightX18` selects `longWeightInitialX18` when `amount >= 0`: [5](#0-4) 

So `CoreRisk.longWeight` is always the long weight. In `getHealth`, the spread adjustment is:

```
health += basisAmount * (spotPrice + perpPrice) * (spreadWeight - existingWeight)
``` [6](#0-5) 

For a **short-spot/long-perp** spread:
- The spot's actual health contribution (from `getHealthContribution`) applied `shortWeightInitialX18` (≥ 1.0) because `amount < 0`.
- But `existingWeight` uses `spotCoreRisk.longWeight` = `longWeightInitialX18` (≤ 1.0).
- `existingWeight` is therefore **lower** than the actual weight already applied.
- `(spreadWeight - existingWeight)` is **larger** than correct.
- The health bonus is **inflated**.

The guard at lines 103–108 only filters out same-direction positions; it does not prevent this miscalculation: [7](#0-6) 

### Impact Explanation

An inflated health bonus means the subaccount appears healthier than it is. The trader can open or maintain positions with more leverage than the risk parameters permit. If the market moves against the position, the protocol may be unable to liquidate in time, resulting in bad debt absorbed by the insurance fund. This directly matches the Critical scope: inflated health allows more leverage than permitted, creating protocol bad debt risk.

### Likelihood Explanation

Any trader holding a short-spot/long-perp spread (a common basis trade) on any spread pair registered in `spreads` will trigger this path on every `getHealth` call. No special privileges are required. The asymmetry between long and short weights is a normal, expected configuration enforced by `_addOrUpdateProduct`. The effect is proportional to the gap between `shortWeightInitial` and `longWeightInitial`.

### Recommendation

In `BaseEngine.getCoreRisk`, pass the actual `amount` (not `1`) to `_getWeightX18` so that `CoreRisk.longWeight` reflects the directional weight of the position:

```solidity
RiskHelper._getWeightX18(risk, amount, healthType)
```

Alternatively, rename the field and update `getHealth` to select the correct directional weight based on `CoreRisk.amount` before computing `existingWeight`.

### Proof of Concept

1. Deploy with a spread pair (spotId, perpId) where `longWeightInitial = 0.9e9`, `shortWeightInitial = 1.1e9`.
2. Open a short-spot/long-perp position of equal size.
3. Call `getHealth(subaccount, INITIAL)`.
4. Observe: `existingWeight = (0.9e18 + 0.9e18) / 2 = 0.9e18` (uses long weight for both).
5. Correct `existingWeight` should be `(1.1e18 + 0.9e18) / 2 = 1.0e18`.
6. `(spreadWeight - existingWeight)` is `0.1e18` larger than correct, inflating the health bonus by `basisAmount * (spotPrice + perpPrice) * 0.1e18`.
7. Compare `getHealth` output against a reference implementation using directional weights; the difference equals the inflated bonus.

### Citations

**File:** core/contracts/BaseEngine.sol (L179-192)
```text
    function getCoreRisk(
        bytes32 subaccount,
        uint32 productId,
        IProductEngine.HealthType healthType
    ) external returns (IProductEngine.CoreRisk memory) {
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

**File:** core/contracts/BaseEngine.sol (L236-240)
```text
            riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
                riskStore.longWeightMaintenance <= 10**9 &&
                riskStore.shortWeightInitial >=
                riskStore.shortWeightMaintenance &&
                riskStore.shortWeightMaintenance >= 10**9,
```

**File:** core/contracts/Clearinghouse.sol (L103-108)
```text
            if (
                (spotCoreRisk.amount == 0) ||
                ((spotCoreRisk.amount > 0) == (perpCoreRisk.amount > 0))
            ) {
                continue;
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
