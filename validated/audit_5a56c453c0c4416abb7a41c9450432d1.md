### Title
Incorrect Weight Argument in `getCoreRisk` Inflates Spread Health Bonus for Short Legs — (`File: core/contracts/BaseEngine.sol`)

---

### Summary

`BaseEngine.getCoreRisk` always passes the hardcoded value `1` to `RiskHelper._getWeightX18`, unconditionally returning the **long weight** regardless of whether the actual position is short. This `longWeight` field is then consumed by `Clearinghouse.getHealth` to compute `existingWeight` in the spread health adjustment. For any spread position where one leg is short, `existingWeight` is understated, inflating the spread health bonus and allowing undercollateralized spread positions.

---

### Finding Description

`BaseEngine.getCoreRisk` retrieves the subaccount's actual balance but discards its sign when computing the weight:

```solidity
// BaseEngine.sol lines 179–192
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
            RiskHelper._getWeightX18(risk, 1, healthType)  // ← always 1, not `amount`
        );
}
``` [1](#0-0) 

`_getWeightX18` branches on the sign of its second argument to select long vs. short weight:

```solidity
// RiskHelper.sol lines 34–55
function _getWeightX18(Risk memory risk, int128 amount, ...) internal pure returns (int128) {
    if (amount >= 0) {
        weight = ... risk.longWeightInitialX18 ...;   // < 1
    } else {
        weight = ... risk.shortWeightInitialX18 ...;  // > 1
    }
}
``` [2](#0-1) 

Because `1` is always passed, `getCoreRisk` always returns `longWeightInitialX18` (or maintenance equivalent), even when `amount < 0`.

`Clearinghouse.getHealth` uses the returned `longWeight` to compute the spread health adjustment:

```solidity
// Clearinghouse.sol lines 125–135
int128 existingWeight = (spotCoreRisk.longWeight + perpCoreRisk.longWeight) / 2;
int128 spreadWeight = RiskHelper._getSpreadWeightX18(perpCoreRisk, spotCoreRisk, healthType);
health += basisAmount
    .mul(spotCoreRisk.price + perpCoreRisk.price)
    .mul(spreadWeight - existingWeight);
``` [3](#0-2) 

`_getSpreadWeightX18` also consumes `perpCoreRisk.longWeight` directly:

```solidity
// RiskHelper.sol lines 57–81
if (spotCoreRisk.amount > 0) {
    spreadWeight = ONE - (ONE - perpCoreRisk.longWeight) / 5;
}
``` [4](#0-3) 

**Concrete scenario — long spot + short perp spread:**

| Quantity | Correct value | Buggy value |
|---|---|---|
| `perpCoreRisk.longWeight` | `shortWeightInitialX18` ≈ 1.1 | `longWeightInitialX18` ≈ 0.9 |
| `existingWeight` | `(0.9 + 1.1)/2 = 1.0` | `(0.9 + 0.9)/2 = 0.9` |
| `spreadWeight` | `1 − (1 − 1.1)/5 = 1.02` | `1 − (1 − 0.9)/5 = 0.98` |
| `spreadWeight − existingWeight` | `1.02 − 1.0 = 0.02` | `0.98 − 0.9 = 0.08` |

The spread health bonus is **4× larger** than it should be. The individual position health contributions in `_calculateProductHealth` correctly use the actual `amount` (and thus `shortWeightPerp` for the short leg), but the spread adjustment undoes part of that penalty using the wrong baseline. [5](#0-4) 

---

### Impact Explanation

The inflated spread health bonus allows a user holding a long spot + short perp (or short spot + long perp) spread to pass `getHealth` checks with less collateral than the protocol requires. This is consumed in every health-gated operation: withdrawals, additional borrows, and liquidation eligibility checks. A user can deliberately construct a spread position to exploit the inflated health, borrow against it, and leave the protocol with an undercollateralized subaccount. This is a direct solvency/accounting corruption.

---

### Likelihood Explanation

Any unprivileged user can open a spread position through normal trading via `OffchainExchange`. The spread health path in `Clearinghouse.getHealth` is exercised on every health-gated transaction. No special privileges, governance capture, or external dependency failure is required. The trigger is deterministic and repeatable.

---

### Recommendation

In `BaseEngine.getCoreRisk`, pass the actual `amount` to `_getWeightX18` instead of the hardcoded `1`:

```solidity
// Before (buggy):
RiskHelper._getWeightX18(risk, 1, healthType)

// After (correct):
RiskHelper._getWeightX18(risk, amount, healthType)
```

This ensures `CoreRisk.longWeight` (which should semantically be "the weight for this position's actual direction") correctly reflects whether the position is long or short, so `existingWeight` in the spread calculation matches the weights already applied by `_calculateProductHealth`.

---

### Proof of Concept

1. User opens a long spot position of size `S` on product `spotId` and a short perp position of size `S` on product `perpId` (a classic basis trade).
2. User calls a health-gated operation (e.g., withdraw quote).
3. `Clearinghouse.getHealth` calls `perpEngine.getCoreRisk(subaccount, perpId, INITIAL)`.
4. `getCoreRisk` fetches `amount = -S` but calls `_getWeightX18(risk, 1, INITIAL)`, returning `longWeightInitialX18 ≈ 0.9` instead of `shortWeightInitialX18 ≈ 1.1`.
5. `existingWeight = (0.9 + 0.9)/2 = 0.9`; correct value is `(0.9 + 1.1)/2 = 1.0`.
6. `spreadWeight = 0.98`; correct value is `1.02`.
7. Spread health bonus = `S * 2P * 0.08` instead of `S * 2P * 0.02` — a 4× overstatement.
8. User passes the health check and withdraws collateral that should have been locked, leaving the subaccount undercollateralized.

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
