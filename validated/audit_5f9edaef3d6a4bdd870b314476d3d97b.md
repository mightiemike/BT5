### Title
Immediate Risk Weight Update Enables Back-Running Liquidation of Healthy Positions Without Grace Period — (`File: core/contracts/BaseEngine.sol`)

---

### Summary

`updateRisk()` in `BaseEngine.sol`, callable via `spotUpdateRisk()` / `perpUpdateRisk()` in `ContractOwner.sol`, immediately overwrites `longWeightMaintenance` and `shortWeightMaintenance` for any product with no delay or grace period. Because these weights are the direct inputs to maintenance health calculation, a previously healthy subaccount can be rendered liquidatable in the same block, allowing any liquidator to back-run the owner's risk update and liquidate the position before the user can respond.

---

### Finding Description

`BaseEngine.updateRisk()` writes the new `RiskStore` directly to storage in a single atomic step: [1](#0-0) 

This is exposed to the owner through `ContractOwner.spotUpdateRisk()` and `ContractOwner.perpUpdateRisk()` with no timelock, pending state, or grace period: [2](#0-1) 

The maintenance health of a subaccount is computed by `RiskHelper._getWeightX18()`, which reads `longWeightMaintenanceX18` (for long positions) or `shortWeightMaintenanceX18` (for short positions) directly from the stored `Risk`: [3](#0-2) 

This feeds into `BaseEngine.getHealthContribution()` → `Clearinghouse.getHealth()` → `ClearinghouseLiq.isUnderMaintenance()`: [4](#0-3) 

`isUnderMaintenance` is the sole gate for liquidation eligibility: [5](#0-4) 

Because the weight update is instantaneous and the liquidation check reads the live weight, any subaccount that was healthy before the update can be liquidated in the very next transaction.

---

### Impact Explanation

A user holding a long spot or perp position with maintenance health marginally above zero can be liquidated immediately after the owner lowers `longWeightMaintenance` (or raises `shortWeightMaintenance` for shorts). The user loses their position at a liquidation discount with no opportunity to add collateral or close voluntarily. The corrupted state is the subaccount's asset balance: the liquidatee's position is forcibly transferred to the liquidator at a below-market price, resulting in a direct, irreversible asset loss for the user.

---

### Likelihood Explanation

The owner can call `spotUpdateRisk` or `perpUpdateRisk` at any time. A liquidator monitoring the mempool (or the sequencer itself, since transactions are submitted via `Endpoint.submitTransactions`) can observe the risk update and immediately submit a `liquidateSubaccount` transaction targeting any subaccount that the new weights render unhealthy. No special permissions are required for the liquidator — any address can be the `txn.sender` in a liquidation. The sequencer-based architecture means the sequencer can order the liquidation transaction directly after the risk update in the same batch, making this trivially exploitable without even requiring mempool visibility.

---

### Recommendation

Introduce a two-step timelock for risk weight reductions (i.e., changes that tighten maintenance requirements):

1. **Stage the update**: Store the pending `RiskStore` and the timestamp at which it becomes effective (e.g., `block.timestamp + GRACE_PERIOD`).
2. **Apply the update**: A second call, only executable after the grace period has elapsed, commits the new weights to storage.

Alternatively, emit an event when a risk update is staged and enforce a minimum grace period (e.g., 24 hours) before the new, stricter weights take effect for existing positions. New positions opened after the staging can be subject to the new weights immediately.

---

### Proof of Concept

1. Alice opens a long perp position. Her maintenance health is `+5` (healthy).
2. Owner calls `ContractOwner.perpUpdateRisk(productId, newRiskStore)` where `newRiskStore.longWeightMaintenance` is reduced from `0.95e9` to `0.80e9`.
3. `BaseEngine.updateRisk()` immediately writes the new weight: `_risk().value[productId] = riskStore` (`BaseEngine.sol:289`).
4. Alice's maintenance health is now recomputed using `longWeightMaintenanceX18 = 0.80e18`, dropping her health to `-10`.
5. A liquidator (or the sequencer) calls `liquidateSubaccount` in the same block or the next sequencer batch.
6. `isUnderMaintenance(alice)` returns `true` (`ClearinghouseLiq.sol:51-58`), the `require` at line 603 passes, and Alice's position is liquidated at a discount.
7. Alice had no opportunity to add collateral or close her position between steps 2 and 6. [1](#0-0) [2](#0-1) [6](#0-5) [5](#0-4)

### Citations

**File:** core/contracts/BaseEngine.sol (L278-290)
```text
    function updateRisk(uint32 productId, RiskHelper.RiskStore memory riskStore)
        external
        onlyOwner
    {
        require(
            riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
                riskStore.shortWeightInitial >=
                riskStore.shortWeightMaintenance,
            ERR_BAD_PRODUCT_CONFIG
        );

        _risk().value[productId] = riskStore;
    }
```

**File:** core/contracts/ContractOwner.sol (L453-465)
```text
    function spotUpdateRisk(
        uint32 productId,
        RiskHelper.RiskStore memory riskStore
    ) external onlyOwner {
        spotEngine.updateRisk(productId, riskStore);
    }

    function perpUpdateRisk(
        uint32 productId,
        RiskHelper.RiskStore memory riskStore
    ) external onlyOwner {
        perpEngine.updateRisk(productId, riskStore);
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

**File:** core/contracts/ClearinghouseLiq.sol (L51-58)
```text
    function isUnderMaintenance(bytes32 subaccount) internal returns (bool) {
        // Weighted maintenance health < 0
        return
            getHealthFromClearinghouse(
                subaccount,
                IProductEngine.HealthType.MAINTENANCE
            ) < 0;
    }
```

**File:** core/contracts/ClearinghouseLiq.sol (L601-603)
```text
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
```
