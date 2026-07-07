### Title
Over-Liquidation Allowed Due to Wrong Health Type in `_handleLiquidationPayment` - (File: `core/contracts/ClearinghouseLiq.sol`)

---

### Summary

The over-liquidation guard in `_handleLiquidationPayment` uses `isAboveInitial` (which evaluates `INITIAL` health) instead of a maintenance-level check. Because `INITIAL` health is strictly harder to achieve than `MAINTENANCE` health, a liquidator can liquidate a subaccount beyond the point where its maintenance health is restored, as long as its initial health remains negative. This is a direct analog to the reported bug: both bugs select the wrong level of an ordered structure — the reported bug picks the highest-ICR trove instead of the lowest-ICR trove; this bug picks the stricter health threshold instead of the correct one.

---

### Finding Description

`ClearinghouseLiq.sol` defines three health helpers: [1](#0-0) 

The protocol enforces at product registration time that INITIAL weights are always strictly more conservative than MAINTENANCE weights: [2](#0-1) 

This means `INITIAL health ≤ MAINTENANCE health` for every subaccount. A subaccount can therefore have:
- `MAINTENANCE health > 0` (no longer eligible for liquidation), and simultaneously
- `INITIAL health < 0` (still below the initial threshold)

The over-liquidation guard at the end of `_handleLiquidationPayment` is: [3](#0-2) 

`isAboveInitial` returns `true` only when `INITIAL health > 0`. Because INITIAL health is the stricter (lower) value, the guard does **not** fire when `MAINTENANCE health > 0` but `INITIAL health ≤ 0`. A liquidator can therefore submit a `txn.amount` that drives the liquidatee's maintenance health positive while keeping initial health negative, and the `ERR_LIQUIDATED_TOO_MUCH` revert never triggers.

The entry gate correctly uses maintenance health: [4](#0-3) 

So the protocol opens liquidation at `MAINTENANCE health < 0` but closes it only at `INITIAL health > 0` — a wider window than intended.

---

### Impact Explanation

A liquidator can seize more collateral than is necessary to restore a subaccount to solvency. The liquidatee's position is reduced beyond the maintenance-health restoration point, causing direct asset loss to the liquidatee. The liquidator profits from the excess liquidation discount applied to the over-liquidated portion.

---

### Likelihood Explanation

Any address can call `liquidateSubaccountImpl` through the endpoint once a subaccount's maintenance health is negative. The liquidator fully controls `txn.amount` and can deliberately choose a value that restores maintenance health while keeping initial health negative. No special privileges, governance access, or external conditions are required beyond a normally liquidatable subaccount existing in the system.

---

### Recommendation

Replace `isAboveInitial` with a maintenance-level check in the over-liquidation guard. Since `isAboveMaintenance` does not currently exist as a helper, add it analogously to `isUnderMaintenance`:

```solidity
function isAboveMaintenance(bytes32 subaccount) internal returns (bool) {
    return getHealthFromClearinghouse(
        subaccount,
        IProductEngine.HealthType.MAINTENANCE
    ) > 0;
}
```

Then change line 573 to:

```solidity
require(!isAboveMaintenance(txn.liquidatee), ERR_LIQUIDATED_TOO_MUCH);
```

This ensures liquidation is halted as soon as the liquidatee's maintenance health is restored, consistent with the entry condition at line 603.

---

### Proof of Concept

1. Subaccount `A` has `MAINTENANCE health = -10`, `INITIAL health = -50`.
2. Liquidator calls `liquidateSubaccountImpl` with `txn.amount` chosen to move `A` to `MAINTENANCE health = +5`, `INITIAL health = -20`.
3. `isUnderMaintenance(A)` at entry (line 603) returns `true` → liquidation proceeds.
4. After `_handleLiquidationPayment`, `isAboveInitial(A)` at line 573 evaluates `INITIAL health (-20) > 0` → `false` → `require(!false)` passes.
5. The liquidation completes even though `A`'s maintenance health is now positive, meaning `A` was over-liquidated.
6. In a subsequent block, `isUnderMaintenance(A)` returns `false`, so no further liquidation is possible — but the damage (excess asset seizure) has already occurred. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L33-58)
```text
    function isUnderInitial(bytes32 subaccount) public returns (bool) {
        // Weighted initial health with limit orders < 0
        return
            getHealthFromClearinghouse(
                subaccount,
                IProductEngine.HealthType.INITIAL
            ) < 0;
    }

    function isAboveInitial(bytes32 subaccount) public returns (bool) {
        // Weighted initial health with limit orders < 0
        return
            getHealthFromClearinghouse(
                subaccount,
                IProductEngine.HealthType.INITIAL
            ) > 0;
    }

    function isUnderMaintenance(bytes32 subaccount) internal returns (bool) {
        // Weighted maintenance health < 0
        return
            getHealthFromClearinghouse(
                subaccount,
                IProductEngine.HealthType.MAINTENANCE
            ) < 0;
    }
```

**File:** core/contracts/ClearinghouseLiq.sol (L572-577)
```text
        // it's ok to let initial health become 0
        require(!isAboveInitial(txn.liquidatee), ERR_LIQUIDATED_TOO_MUCH);
        require(
            txn.sender == N_ACCOUNT || !isUnderInitial(txn.sender),
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/ClearinghouseLiq.sol (L603-603)
```text
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
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

**File:** core/contracts/libraries/RiskHelper.sol (L44-52)
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
```
