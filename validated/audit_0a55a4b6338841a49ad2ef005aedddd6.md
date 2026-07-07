Now I have all the context needed. Let me analyze the critical finding.

In `BaseEngine.getCoreRisk`, the weight is computed with a **hardcoded `amount = 1`** regardless of the actual position direction: [1](#0-0) 

This means `CoreRisk.longWeight` is always the **long weight**, even for short positions. This feeds directly into `Clearinghouse.getHealth`'s spread adjustment: [2](#0-1) 

And into `RiskHelper._getSpreadWeightX18`: [3](#0-2) 

---

### Title
Hardcoded `amount = 1` in `getCoreRisk` Causes Spread Health to Be Systematically Overstated, Enabling Undercollateralized Borrowing — (File: `core/contracts/BaseEngine.sol`)

### Summary
`BaseEngine.getCoreRisk` always passes `amount = 1` to `RiskHelper._getWeightX18`, returning `longWeight` regardless of whether the actual position is long or short. This corrupts the spread health adjustment in `Clearinghouse.getHealth`, causing `existingWeight` to be understated and the health bonus to be overstated. Any user holding a spread position (long spot / short perp) can exploit this to borrow significantly more collateral than the protocol intends to allow.

### Finding Description
`BaseEngine.getCoreRisk` fetches the real position `amount` from storage but then discards it when computing the weight:

```solidity
// BaseEngine.sol line 185-191
(int128 amount, ) = _getBalance(productId, subaccount);
return
    IProductEngine.CoreRisk(
        amount,
        risk.priceX18,
        RiskHelper._getWeightX18(risk, 1, healthType)  // ← hardcoded 1, not `amount`
    );
```

`_getWeightX18` branches on the sign of `amount`: positive → `longWeight`, negative → `shortWeight`. By always passing `1`, the function always returns `longWeight` even for short positions. [4](#0-3) 

In `Clearinghouse.getHealth`, the spread adjustment is:

```solidity
int128 existingWeight = (spotCoreRisk.longWeight + perpCoreRisk.longWeight) / 2;
int128 spreadWeight   = RiskHelper._getSpreadWeightX18(perpCoreRisk, spotCoreRisk, healthType);
health += basisAmount.mul(spotCoreRisk.price + perpCoreRisk.price).mul(spreadWeight - existingWeight);
``` [5](#0-4) 

For a **long spot / short perp** spread:
- `spotCoreRisk.longWeight` = `spotLongWeight` ✓ (correct — spot is long)
- `perpCoreRisk.longWeight` = `perpLongWeight` ✗ (wrong — perp is short, should be `perpShortWeight`)

Since `perpLongWeight < 1 < perpShortWeight`, `existingWeight` is **understated**, making `spreadWeight − existingWeight` **overstated**, and the health bonus **inflated**.

`_getSpreadWeightX18` also uses `perpCoreRisk.longWeight` to derive `spreadWeight`:

```solidity
if (spotCoreRisk.amount > 0) {
    spreadWeight = ONE - (ONE - perpCoreRisk.longWeight) / 5;
``` [6](#0-5) 

Using `perpLongWeight = 0.9` instead of `perpShortWeight = 1.1` produces a lower `spreadWeight` (0.98 vs. capped 0.994), but the dominant error is in `existingWeight`.

### Impact Explanation
For typical risk parameters (`longWeightMaintenance = 0.9`, `shortWeightMaintenance = 1.1`):

| | Incorrect (bug) | Correct |
|---|---|---|
| `existingWeight` | `(0.9 + 0.9)/2 = 0.90` | `(0.9 + 1.1)/2 = 1.00` |
| `spreadWeight` | `0.98` | `0.994` (capped) |
| `spreadWeight − existingWeight` | `+0.08` | `−0.006` |
| Health adj. (1 BTC @ $1000) | `+$160` | `−$12` |

A user holding 1 BTC of long-spot/short-perp spread receives **$172 more health credit than entitled**. Scaled to 100 BTC, this is $17,200 of phantom collateral. The user can withdraw or borrow against this phantom health, leaving the protocol with an undercollateralized subaccount that cannot be fully liquidated, draining the insurance fund.

### Likelihood Explanation
The trigger is fully unprivileged: any trader can open a spot/perp spread via `OffchainExchange.matchOrders` and immediately call `Clearinghouse.withdrawCollateral`. The spread product pairs are registered at protocol initialization and are always available. No special role, governance action, or oracle manipulation is required. The overstatement scales linearly with position size, making it economically rational to exploit at scale.

### Recommendation
In `BaseEngine.getCoreRisk`, replace the hardcoded `1` with the actual position `amount`:

```solidity
// Before (buggy):
RiskHelper._getWeightX18(risk, 1, healthType)

// After (correct):
RiskHelper._getWeightX18(risk, amount, healthType)
``` [7](#0-6) 

This ensures that short positions return `shortWeight`, so `existingWeight` correctly reflects the health already attributed by the individual engines, and the spread adjustment is computed on the true marginal benefit of the hedge.

### Proof of Concept
1. Trader opens: long 10 BTC spot (productId = 2), short 10 BTC perp (productId = 3) via `OffchainExchange.matchOrders`. BTC oracle price = $1,000.
2. `Clearinghouse.getHealth` is called (e.g., during `withdrawCollateral`).
3. `spotEngine.getCoreRisk(subaccount, 2, MAINTENANCE)` returns `longWeight = 0.9` ✓
4. `perpEngine.getCoreRisk(subaccount, 3, MAINTENANCE)` returns `longWeight = 0.9` ✗ (actual position is short; correct value is `shortWeight = 1.1`)
5. `existingWeight = (0.9 + 0.9) / 2 = 0.9`; `spreadWeight = 0.98`; adjustment = `10 * $2000 * 0.08 = +$1,600`
6. Correct adjustment would be `10 * $2000 * (0.994 − 1.0) = −$120`
7. Phantom health surplus = **$1,720**
8. Trader calls `Clearinghouse.withdrawCollateral` for $1,720 more than the protocol should allow.
9. If BTC drops and the position is liquidated, the insurance fund absorbs the $1,720 shortfall per 10 BTC of spread — scalable to protocol insolvency with large enough positions.

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

**File:** core/contracts/Clearinghouse.sol (L124-136)
```text
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
