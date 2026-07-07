### Title
Spread Health Adjustment Uses Mismatched Weight View (`longWeight` vs. Actual Direction Weight), Inflating Health for Spread Positions — (File: `core/contracts/BaseEngine.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.getHealth()` computes a spread health adjustment using `existingWeight` derived from `getCoreRisk()`, which always returns `longWeight` (hardcoded `amount = 1`). However, `getHealthContribution()` already computed health using the actual position direction (long or short weight). This two-view mismatch causes the spread adjustment to be calculated against the wrong baseline weight, inflating the health bonus for spread positions where the perp leg is short.

---

### Finding Description

In `Clearinghouse.getHealth()`, after summing health contributions from both engines, a spread adjustment is applied:

```solidity
int128 existingWeight = (spotCoreRisk.longWeight +
    perpCoreRisk.longWeight) / 2;
int128 spreadWeight = RiskHelper._getSpreadWeightX18(...);
health += basisAmount
    .mul(spotCoreRisk.price + perpCoreRisk.price)
    .mul(spreadWeight - existingWeight);
```

`existingWeight` is sourced from `getCoreRisk()` in `BaseEngine.sol`:

```solidity
return IProductEngine.CoreRisk(
    amount,
    risk.priceX18,
    RiskHelper._getWeightX18(risk, 1, healthType)  // hardcoded 1 → always longWeight
);
```

But `_calculateProductHealth()` — which already ran inside `getHealthContribution()` — uses the actual balance amount to select the weight:

```solidity
int128 weight = RiskHelper._getWeightX18(risk, amount, healthType);
health += amount.mul(weight).mul(risk.priceX18);
```

For a standard spread (spot long + perp short):
- `getHealthContribution` applied `perpShortWeight` (e.g., `1.1e18`) to the perp leg
- `getCoreRisk` returns `perpLongWeight` (e.g., `0.9e18`) for the same leg
- `existingWeight = (spotLongWeight + perpLongWeight) / 2 = 0.9` instead of the correct `(spotLongWeight + perpShortWeight) / 2 = 1.0`

With `spreadWeight ≈ 0.98` (derived from `perpLongWeight`):
- Correct: `spreadWeight − existingWeight = 0.98 − 1.0 = −0.02` → health **penalty**
- Actual: `spreadWeight − existingWeight = 0.98 − 0.9 = +0.08` → health **bonus**

The sign of the adjustment flips, and the magnitude error is `0.1 × basisAmount × (spotPrice + perpPrice)`.

---

### Impact Explanation

The spread health adjustment is miscalculated for any spread position where the perp leg is short (the dominant spread trade: long spot, short perp to capture funding). The protocol grants a health **bonus** where it should apply a health **penalty**, overstating the subaccount's health. This allows the user to:

- Borrow more quote collateral than the risk parameters permit
- Open larger positions than the protocol's leverage limits intend
- Remain unliquidatable longer than they should be, increasing bad-debt risk to the insurance fund

All health-gated operations are affected: `withdrawCollateral`, `mintNlp`, `burnNlp`, `transferQuote`, and liquidation eligibility checks in `ClearinghouseLiq`.

---

### Likelihood Explanation

The spread trade (long spot + short perp) is a core, explicitly supported strategy in Nado — the `spreads` bitmap is a first-class protocol concept. Any user holding such a position triggers this miscalculation on every health check. No special permissions or unusual conditions are required. The trigger is a normal, unprivileged user action.

---

### Recommendation

In `BaseEngine.getCoreRisk()`, pass the actual subaccount balance `amount` to `_getWeightX18` instead of the hardcoded `1`, so the returned weight reflects the actual position direction:

```solidity
// Before (incorrect):
RiskHelper._getWeightX18(risk, 1, healthType)

// After (correct):
RiskHelper._getWeightX18(risk, amount, healthType)
```

This ensures `existingWeight` in `Clearinghouse.getHealth()` matches the weight that `getHealthContribution()` actually applied, eliminating the two-view mismatch.

---

### Proof of Concept

**Setup**: Perp product with `longWeightInitial = 0.9e9`, `shortWeightInitial = 1.1e9`. Spread: 1 unit spot long + 1 unit perp short. Both prices = `1e18`.

**Step 1 — `getHealthContribution` (actual weights used):**
- Spot: `+1 × 0.9 × 1 = +0.9`
- Perp: `−1 × 1.1 × 1 = −1.1`
- Net health from spread portion: `−0.2`

**Step 2 — `getCoreRisk` (mismatched view):**
- `spotCoreRisk.longWeight = 0.9`, `perpCoreRisk.longWeight = 0.9`
- `existingWeight = (0.9 + 0.9) / 2 = 0.9`
- `spreadWeight = 1 − (1 − 0.9) / 5 = 0.98`
- Adjustment: `1 × (1 + 1) × (0.98 − 0.9) = +0.16`

**Step 3 — Total spread health = `−0.2 + 0.16 = −0.04`**

**Correct calculation** (using `perpShortWeight = 1.1`):
- `existingWeight = (0.9 + 1.1) / 2 = 1.0`
- `spreadWeight = 0.98` (unchanged, since it also uses `perpLongWeight` — a secondary issue)
- Adjustment: `1 × 2 × (0.98 − 1.0) = −0.04`
- Total spread health = `−0.2 + (−0.04) = −0.24`

The protocol reports `−0.04` health for the spread portion; the correct value is `−0.24`. The user receives `0.20` units of excess health credit per unit of spread position, directly exploitable by opening large spread positions to unlock additional borrowing capacity. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/BaseEngine.sol (L157-177)
```text
    function _calculateProductHealth(
        uint32 productId,
        bytes32 subaccount,
        IProductEngine.HealthType healthType
    ) internal returns (int128 health) {
        RiskHelper.Risk memory risk = _risk(productId);
        (int128 amount, int128 quoteAmount) = _getBalance(
            productId,
            subaccount
        );
        int128 weight = RiskHelper._getWeightX18(risk, amount, healthType);
        health += quoteAmount;

        if (amount != 0) {
            if (weight == 2 * ONE) {
                return -INF;
            }
            health += amount.mul(weight).mul(risk.priceX18);
            emit PriceQuery(productId);
        }
    }
```

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

**File:** core/contracts/Clearinghouse.sol (L123-138)
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
            emit PriceQuery(_spotId);
            emit PriceQuery(_perpId);
        }
```

**File:** core/contracts/libraries/RiskHelper.sol (L34-55)
```text
    function _getWeightX18(
        Risk memory risk,
        int128 amount,
        IProductEngine.HealthType healthType
    ) internal pure returns (int128) {
        if (healthType == IProductEngine.HealthType.PNL) {
            return ONE;
        }

        int128 weight;
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
    }
```
