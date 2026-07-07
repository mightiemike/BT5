### Title
Spread Health Adjustment Uses Incorrect `existingWeight` Due to Mismatched Long/Short Weights — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

In `Clearinghouse.getHealth()`, the spread health adjustment computes `existingWeight` as the average of the two `longWeight` values returned by `getCoreRisk()`. However, `getCoreRisk()` always returns the **long** weight regardless of the actual position direction. For the short leg of a spread (e.g., a short perp in a long-spot/short-perp basis trade), the actual health contribution in `_calculateProductHealth()` uses `shortWeightInitialX18`, not `longWeightInitialX18`. Since `shortWeight >= 1 > longWeight`, `existingWeight` is systematically underestimated, inflating the spread health bonus and allowing users to be under-collateralized.

---

### Finding Description

`Clearinghouse.getHealth()` applies a spread adjustment to reward basis positions (long spot + short perp, or short spot + long perp) with reduced margin requirements:

```solidity
int128 existingWeight = (spotCoreRisk.longWeight + perpCoreRisk.longWeight) / 2;
int128 spreadWeight = RiskHelper._getSpreadWeightX18(perpCoreRisk, spotCoreRisk, healthType);

health += basisAmount
    .mul(spotCoreRisk.price + perpCoreRisk.price)
    .mul(spreadWeight - existingWeight);
``` [1](#0-0) 

`existingWeight` is intended to represent the weight already applied to the spread legs in the individual product health contributions. It is computed from `CoreRisk.longWeight` for both legs.

However, `getCoreRisk()` in `BaseEngine` always passes the literal `1` (positive) to `_getWeightX18()`, unconditionally returning the **long** weight:

```solidity
return IProductEngine.CoreRisk(
    amount,
    risk.priceX18,
    RiskHelper._getWeightX18(risk, 1, healthType)  // always longWeight
);
``` [2](#0-1) 

In contrast, `_calculateProductHealth()` passes the **actual** `amount` to `_getWeightX18()`, so a short position (negative amount) correctly uses `shortWeightInitialX18`:

```solidity
int128 weight = RiskHelper._getWeightX18(risk, amount, healthType);
``` [3](#0-2) 

`_getWeightX18` selects `shortWeightInitialX18` when `amount < 0`:

```solidity
if (amount >= 0) {
    weight = healthType == IProductEngine.HealthType.INITIAL
        ? risk.longWeightInitialX18
        : risk.longWeightMaintenanceX18;
} else {
    weight = healthType == IProductEngine.HealthType.INITIAL
        ? risk.shortWeightInitialX18
        : risk.shortWeightMaintenanceX18;
}
``` [4](#0-3) 

The protocol enforces `shortWeightMaintenance >= 10^9` (i.e., `shortWeight >= 1`) and `longWeightMaintenance <= 10^9` (i.e., `longWeight <= 1`):

```solidity
require(
    riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
        riskStore.longWeightMaintenance <= 10**9 &&
        riskStore.shortWeightInitial >= riskStore.shortWeightMaintenance &&
        riskStore.shortWeightMaintenance >= 10**9,
    ERR_BAD_PRODUCT_CONFIG
);
``` [5](#0-4) 

**Concrete mismatch for a long-spot / short-perp spread:**

| Leg | Weight used in `_calculateProductHealth` | Weight used in `existingWeight` |
|---|---|---|
| Long spot | `longWeightInitial_spot` | `longWeightInitial_spot` ✓ |
| Short perp | `shortWeightInitial_perp` (≥ 1) | `longWeightInitial_perp` (≤ 1) ✗ |

Because `shortWeight_perp >= 1 > longWeight_perp`, `existingWeight` is underestimated. This makes `spreadWeight - existingWeight` larger than it should be, inflating the health bonus.

**Numerical example:**
- `longWeight_spot = 0.90`, `longWeight_perp = 0.95`, `shortWeight_perp = 1.05`
- `spreadWeight = 1 - (1 - 0.95)/5 = 0.99`
- Correct `existingWeight = (0.90 + 1.05)/2 = 0.975` → bonus = `0.99 - 0.975 = 0.015`
- Actual `existingWeight = (0.90 + 0.95)/2 = 0.925` → bonus = `0.99 - 0.925 = 0.065`
- **Overestimate: 4.3×**

The same mismatch applies in the reverse direction (short spot / long perp), where `longWeight_spot` is used instead of `shortWeight_spot`.

---

### Impact Explanation

`getHealth()` is the gating check for every collateral-sensitive operation: `withdrawCollateral`, `transferQuote`, `mintNlp`, `burnNlp`, `nlpProfitShare`, and `forceRebalanceNlpPool`. An inflated health score allows a user holding a spread position to:

1. **Withdraw more collateral than is safe**, leaving the subaccount under-collateralized.
2. **Borrow more quote** than the risk model permits.
3. **Delay or prevent liquidation**, since `isUnderMaintenance` also calls `getHealth`.

The magnitude scales with the notional size of the spread position and the gap between `longWeight` and `shortWeight` for the short leg. For large spread positions with wide weight gaps, the over-credit can be material enough to cause bad debt. [6](#0-5) 

---

### Likelihood Explanation

**Medium.** Spread positions are a first-class, explicitly supported feature of the protocol (the `spreads` bitmask is set at initialization and used throughout). Any unprivileged user can open a long-spot/short-perp basis position through the `OffchainExchange` and then call `withdrawCollateral` via `Endpoint`. The bug is always active whenever `shortWeight != longWeight` for the short leg, which is the normal operating condition enforced by `ERR_BAD_PRODUCT_CONFIG`. No special permissions or governance actions are required.

---

### Recommendation

In `Clearinghouse.getHealth()`, compute `existingWeight` using the **actual** weights applied to each leg, not the long weights from `getCoreRisk`. Either:

1. Extend `CoreRisk` to carry the actual directional weight (based on the real `amount` sign), and use those in `existingWeight`; or
2. Compute the spread adjustment as the exact delta between the desired spread health and the already-computed individual health contributions, avoiding the averaged `existingWeight` approximation entirely.

The corrected `existingWeight` for a long-spot / short-perp spread should use `shortWeightInitial_perp` for the perp leg, not `longWeightInitial_perp`.

---

### Proof of Concept

1. Deploy the protocol with a spread pair: spot product `S` (`longWeightInitial = 0.90`, `shortWeightInitial = 1.10`) and perp product `P` (`longWeightInitial = 0.95`, `shortWeightInitial = 1.05`).
2. User opens a long-spot / short-perp basis position of size `N` at price `$100` each.
3. Call `getHealth()`:
   - Individual health: `N * 0.90 * 100 + (-N) * 1.05 * 100 = N * (90 - 105) = -15N`
   - `existingWeight = (0.90 + 0.95)/2 = 0.925`
   - `spreadWeight = 1 - (1 - 0.95)/5 = 0.99`
   - Spread adjustment: `N * (100 + 100) * (0.99 - 0.925) = N * 200 * 0.065 = 13N`
   - **Reported health: `-15N + 13N = -2N`**
4. Correct calculation:
   - `existingWeight = (0.90 + 1.05)/2 = 0.975`
   - Spread adjustment: `N * 200 * (0.99 - 0.975) = N * 200 * 0.015 = 3N`
   - **Correct health: `-15N + 3N = -12N`**
5. The protocol reports health of `-2N` instead of `-12N`. For a sufficiently large `N`, the user can withdraw collateral that brings their true health deeply negative, while the protocol's check passes. [7](#0-6)

### Citations

**File:** core/contracts/Clearinghouse.sol (L71-139)
```text
    function getHealth(bytes32 subaccount, IProductEngine.HealthType healthType)
        public
        returns (int128 health)
    {
        ISpotEngine spotEngine = _spotEngine();
        IPerpEngine perpEngine = _perpEngine();

        health = spotEngine.getHealthContribution(subaccount, healthType);
        // min health means that it is attempting to borrow a spot that exists outside
        // of the risk system -- return min health to error out this action
        if (health == -INF) {
            return health;
        }
        health += perpEngine.getHealthContribution(subaccount, healthType);

        uint256 _spreads = spreads;
        while (_spreads != 0) {
            uint32 _spotId = uint32(_spreads & 0xFF);
            _spreads >>= 8;
            uint32 _perpId = uint32(_spreads & 0xFF);
            _spreads >>= 8;

            IProductEngine.CoreRisk memory perpCoreRisk = perpEngine
                .getCoreRisk(subaccount, _perpId, healthType);

            if (perpCoreRisk.amount == 0) {
                continue;
            }

            IProductEngine.CoreRisk memory spotCoreRisk = spotEngine
                .getCoreRisk(subaccount, _spotId, healthType);

            if (
                (spotCoreRisk.amount == 0) ||
                ((spotCoreRisk.amount > 0) == (perpCoreRisk.amount > 0))
            ) {
                continue;
            }

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
            emit PriceQuery(_spotId);
            emit PriceQuery(_perpId);
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L391-420)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
```

**File:** core/contracts/BaseEngine.sol (L162-167)
```text
        RiskHelper.Risk memory risk = _risk(productId);
        (int128 amount, int128 quoteAmount) = _getBalance(
            productId,
            subaccount
        );
        int128 weight = RiskHelper._getWeightX18(risk, amount, healthType);
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
