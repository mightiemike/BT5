Now I have all the information needed. Let me trace through the exact logic.

### Title
`getCoreRisk` Hardcodes `amount=1`, Always Returning Long Weight — Inflates Spread Health for Short-Spot/Long-Perp Basis Trades — (`core/contracts/BaseEngine.sol`, `core/contracts/libraries/RiskHelper.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

`BaseEngine.getCoreRisk` hardcodes `amount=1` when calling `RiskHelper._getWeightX18`, so `CoreRisk.longWeight` always contains the **long-side weight** regardless of the subaccount's actual position direction. `Clearinghouse.getHealth` then uses this field for both `existingWeight` and `_getSpreadWeightX18`. For a short-spot / long-perp basis position, both computations silently substitute the long weight (< ONE) for the correct short weight (≥ ONE), flipping the sign of the spread health adjustment from negative to positive and inflating health.

---

### Finding Description

**Root cause — `BaseEngine.getCoreRisk` line 190:**

```solidity
RiskHelper._getWeightX18(risk, 1, healthType)   // hardcoded 1, always ≥ 0 → always longWeight
```

`_getWeightX18` branches on `amount >= 0`:

```solidity
if (amount >= 0) {
    weight = healthType == INITIAL ? risk.longWeightInitialX18 : risk.longWeightMaintenanceX18;
} else {
    weight = healthType == INITIAL ? risk.shortWeightInitialX18 : risk.shortWeightMaintenanceX18;
}
```

Because `1 >= 0` is always true, `CoreRisk.longWeight` is always the long weight, even when the subaccount holds a short position.

**Downstream error 1 — `existingWeight` in `Clearinghouse.getHealth` lines 125–126:**

```solidity
int128 existingWeight = (spotCoreRisk.longWeight + perpCoreRisk.longWeight) / 2;
```

For short spot + long perp with typical weights (longWeight = 0.9·ONE, shortWeight = 1.1·ONE):
- Buggy: `(0.9 + 0.9)/2 = 0.9`
- Correct: `(1.1 + 0.9)/2 = 1.0`

**Downstream error 2 — `_getSpreadWeightX18` lines 68–69:**

```solidity
} else {
    spreadWeight = ONE - (ONE - spotCoreRisk.longWeight) / 5;
}
```

For short spot (`spotCoreRisk.amount < 0`), the code uses `spotCoreRisk.longWeight` (0.9·ONE) instead of the short weight (1.1·ONE):
- Buggy: `ONE - (ONE - 0.9)/5 = 0.98·ONE` — not capped
- Correct: `ONE - (ONE - 1.1)/5 = 1.02·ONE` → capped at `maxSpreadWeight = 0.99·ONE`

**Net effect on the spread health adjustment (lines 133–135):**

```solidity
health += basisAmount
    .mul(spotCoreRisk.price + perpCoreRisk.price)
    .mul(spreadWeight - existingWeight);
```

For short spot + long perp, `basisAmount` is **positive**:

```solidity
basisAmount = -MathHelper.max(spotCoreRisk.amount, -perpCoreRisk.amount);
// e.g. -max(-10, -10) = 10  → positive
```

| | `spreadWeight` | `existingWeight` | `spreadWeight − existingWeight` | Health adjustment |
|---|---|---|---|---|
| **Buggy** | 0.98 | 0.90 | **+0.08** | **positive (inflated)** |
| **Correct** | 0.99 (capped) | 1.00 | **−0.01** | negative (correct penalty) |

The bug converts a health-reducing adjustment into a health-increasing one. The magnitude of inflation per unit of basis notional is `0.08 − (−0.01) = 0.09·ONE` times `(spotPrice + perpPrice)`.

---

### Impact Explanation

A trader holding a short-spot / long-perp basis position receives a spurious positive spread health bonus instead of the correct small penalty. This allows the subaccount to pass `getHealth(INITIAL) >= 0` checks with less collateral than required, enabling undercollateralized positions. All protocol actions that gate on `getHealth` are affected: `withdrawCollateral`, `transferQuote`, `mintNlp`, `nlpProfitShare`, and the liquidation threshold. The trader can withdraw collateral or open additional positions that would otherwise be blocked, directly threatening protocol solvency.

---

### Likelihood Explanation

The path is fully reachable through normal trading: place a short spot order and a long perp order on a paired product (any entry in the `spreads` bitmap). No special privileges are required. The `spreads` bitmap is set at deployment and covers all listed basis pairs. The miscalculation fires on every `getHealth` call for any subaccount holding such a position.

---

### Recommendation

In `BaseEngine.getCoreRisk`, pass the actual subaccount `amount` to `_getWeightX18` instead of the hardcoded `1`:

```solidity
// Before (line 190):
RiskHelper._getWeightX18(risk, 1, healthType)

// After:
RiskHelper._getWeightX18(risk, amount, healthType)
```

This ensures `CoreRisk.longWeight` reflects the correct directional weight (long weight for long positions, short weight for short positions), fixing both `existingWeight` and `_getSpreadWeightX18` simultaneously. The field should be renamed to `weight` in `CoreRisk` to avoid future confusion.

---

### Proof of Concept

State-machine test (Hardhat/Foundry):

1. Deploy Clearinghouse, SpotEngine, PerpEngine with a spread pair `(spotId=1, perpId=2)`.
2. Set `longWeightInitialX18 = 0.9e18`, `shortWeightInitialX18 = 1.1e18` for both products.
3. Give subaccount `alice` a short spot balance of `-10e18` and a long perp balance of `+10e18` at price `1e18`.
4. Call `Clearinghouse.getHealth(alice, INITIAL)` → record `buggyHealth`.
5. Patch `getCoreRisk` to pass `amount` instead of `1`, redeploy, repeat → record `correctHealth`.
6. Assert `buggyHealth > correctHealth`.

Concrete arithmetic (price = 1, basis = 10):
- Buggy spread adjustment: `10 * 2 * 0.08 = +1.6`
- Correct spread adjustment: `10 * 2 * (−0.01) = −0.2`
- Health inflation: `1.6 − (−0.2) = 1.8` units per 10 notional of basis. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** core/contracts/libraries/RiskHelper.sol (L57-81)
```text
    function _getSpreadWeightX18(
        IProductEngine.CoreRisk memory perpCoreRisk,
        IProductEngine.CoreRisk memory spotCoreRisk,
        IProductEngine.HealthType healthType
    ) internal pure returns (int128) {
        if (healthType == IProductEngine.HealthType.PNL) {
            return ONE;
        }
        int128 spreadWeight;
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
    }
```

**File:** core/contracts/Clearinghouse.sol (L110-135)
```text
            int128 basisAmount;
            if (spotCoreRisk.amount > 0) {
                basisAmount = MathHelper.min(
                    spotCoreRisk.amount,
                    -perpCoreRisk.amount
                );
            } else {
                basisAmount = -MathHelper.max(
                    spotCoreRisk.amount,
                    -perpCoreRisk.amount
                );
            }

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

**File:** core/contracts/interfaces/engine/IProductEngine.sol (L30-34)
```text
    struct CoreRisk {
        int128 amount;
        int128 price;
        int128 longWeight;
    }
```
