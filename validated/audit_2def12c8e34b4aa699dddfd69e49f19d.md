### Title
`getCoreRisk` Hardcoded `amount=1` Returns Wrong Weight for Short Spread Leg, Inflating Health for Net-Short Spread Positions — (`core/contracts/BaseEngine.sol`)

---

### Summary

`BaseEngine.getCoreRisk` passes the literal `1` instead of the subaccount's actual `amount` to `RiskHelper._getWeightX18`. This means the returned `CoreRisk.longWeight` is always the **long** weight, even when the position is short. `Clearinghouse.getHealth` uses this field for both `existingWeight` and `spreadWeight` computation. For a net-short spread (short spot + long perp), this causes the spread health contribution to flip from a **penalty** to a **bonus**, overstating health and enabling undercollateralized positions.

---

### Finding Description

**Root cause — `BaseEngine.getCoreRisk` (line 190):**

```solidity
// core/contracts/BaseEngine.sol:179-192
function getCoreRisk(...) external returns (IProductEngine.CoreRisk memory) {
    RiskHelper.Risk memory risk = _risk(productId);
    (int128 amount, ) = _getBalance(productId, subaccount);
    return IProductEngine.CoreRisk(
        amount,
        risk.priceX18,
        RiskHelper._getWeightX18(risk, 1, healthType)  // ← hardcoded 1, not amount
    );
}
```

`_getWeightX18` branches on `amount >= 0`:

```solidity
// core/contracts/libraries/RiskHelper.sol:44-52
if (amount >= 0) {
    weight = healthType == IProductEngine.HealthType.INITIAL
        ? risk.longWeightInitialX18
        : risk.longWeightMaintenanceX18;
} else {
    weight = healthType == IProductEngine.HealthType.INITIAL
        ? risk.shortWeightInitialX18
        : risk.shortWeightMaintenanceX18;
}
```

Because `1 >= 0` is always true, `getCoreRisk` always returns `longWeightInitialX18` in the `CoreRisk.longWeight` field, regardless of whether the actual position is short.

**Weight constraints** (enforced in `_addOrUpdateProduct`):
- `longWeightInitialX18 ≤ ONE` (e.g., 0.9)
- `shortWeightInitialX18 ≥ ONE` (e.g., 1.1)

**Spread health path — `Clearinghouse.getHealth` (lines 125–135):**

```solidity
int128 existingWeight = (spotCoreRisk.longWeight + perpCoreRisk.longWeight) / 2;
int128 spreadWeight = RiskHelper._getSpreadWeightX18(perpCoreRisk, spotCoreRisk, healthType);
health += basisAmount.mul(spotCoreRisk.price + perpCoreRisk.price).mul(spreadWeight - existingWeight);
```

`_getSpreadWeightX18` for the short-spot branch:

```solidity
// core/contracts/libraries/RiskHelper.sol:68-69
} else {
    spreadWeight = ONE - (ONE - spotCoreRisk.longWeight) / 5;
}
```

This branch is reached when `spotCoreRisk.amount < 0` (short spot). It is designed to use the spot's **short** weight, but because `getCoreRisk` always returns the long weight, it receives `longWeightInitialX18` instead.

**Concrete numerical example** (INITIAL health, spot long=0.9, spot short=1.1, perp long=0.9):

| | With bug (`longWeight=0.9`) | Correct (`shortWeight=1.1`) |
|---|---|---|
| `spreadWeight` | `1 − (1−0.9)/5 = 0.98` | `min(1+(1.1−1)/5, 0.99) = 0.99` |
| `existingWeight` | `(0.9+0.9)/2 = 0.90` | `(1.1+0.9)/2 = 1.00` |
| `spreadWeight − existingWeight` | **+0.08** (bonus) | **−0.01** (penalty) |

The spread health contribution flips sign: a position that should incur a health **penalty** instead receives a health **bonus** of `basisAmount × (spotPrice + perpPrice) × 0.08`.

---

### Impact Explanation

A trader opens a net-short spread (short spot + long perp in a registered pair). Every call to `getHealth(subaccount, INITIAL)` overstates their health by `basisAmount × (spotPrice + perpPrice) × (bugBonus − correctPenalty)`. This allows:

1. **Undercollateralized withdrawals** — the subaccount passes the initial health check with less collateral than required.
2. **Undercollateralized trades** — additional positions can be opened that would otherwise be blocked.
3. **Protocol insolvency risk** — if the position moves against the trader, the protocol may be unable to cover losses.

---

### Likelihood Explanation

- No special privileges required; any user can open a short spot + long perp position in a registered spread pair via the normal order flow.
- The spread health bonus is applied on every `getHealth` call, including withdrawal and order validation paths.
- The precondition (`shortWeightInitialX18 ≠ longWeightInitialX18`) holds for virtually all real assets.

---

### Recommendation

Pass the actual `amount` instead of the hardcoded `1` in `getCoreRisk`:

```solidity
// core/contracts/BaseEngine.sol:190
// Before:
RiskHelper._getWeightX18(risk, 1, healthType)
// After:
RiskHelper._getWeightX18(risk, amount, healthType)
```

This ensures the returned `CoreRisk.longWeight` reflects the correct directional weight (long weight for long positions, short weight for short positions), which is what both `existingWeight` and `_getSpreadWeightX18` depend on for correctness.

---

### Proof of Concept

```solidity
// Pseudocode fuzz test
// spotLongWeightInitial = 0.9e18, spotShortWeightInitial = 1.1e18
// perpLongWeightInitial = 0.9e18

// 1. Open short spot (-10 units) + long perp (+15 units)
// 2. Call getHealth(subaccount, INITIAL)
//    - getCoreRisk(spot) returns longWeight=0.9e18 (BUG: should be 1.1e18)
//    - basisAmount = -max(-10, -15) = 10
//    - spreadWeight = 1 - (1 - 0.9)/5 = 0.98e18
//    - existingWeight = (0.9 + 0.9)/2 = 0.9e18
//    - spread health += 10 * (spotPrice + perpPrice) * 0.08e18  ← INFLATED BONUS
// 3. Assert: getHealth(INITIAL) > getHealth computed with correct shortWeight
//    - Correct: spreadWeight=0.99, existingWeight=1.0, delta=-0.01 → PENALTY
//    - Bug:     spreadWeight=0.98, existingWeight=0.9, delta=+0.08 → BONUS
// 4. Demonstrate withdrawal passes health check with insufficient collateral
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
