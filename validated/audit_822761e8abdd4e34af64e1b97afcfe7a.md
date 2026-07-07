### Title
Collateral Withdrawal Permanently Blocked When Subaccount Is Between Initial and Maintenance Health Thresholds — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

A subaccount whose health falls below the **initial margin threshold** but remains **above the maintenance margin threshold** enters a "twilight zone" where it can neither withdraw collateral nor be liquidated. The user's deposited collateral is locked in the protocol with no on-chain mechanism to recover it until prices move favorably or the user deposits additional funds.

---

### Finding Description

Nado uses two distinct health thresholds backed by separate per-product risk weights:

- **INITIAL health** — computed using `longWeightInitialX18` / `shortWeightInitialX18` (stricter, lower weights)
- **MAINTENANCE health** — computed using `longWeightMaintenanceX18` / `shortWeightMaintenanceX18` (looser, higher weights)

`RiskHelper.RiskStore` stores four independent weight fields, making it structurally guaranteed that `longWeightInitial < longWeightMaintenance` for any properly configured product. [1](#0-0) 

`withdrawCollateral` in `Clearinghouse.sol` enforces the **INITIAL** health check after debiting the balance:

```solidity
IProductEngine.HealthType healthType = sender == X_ACCOUNT
    ? IProductEngine.HealthType.PNL
    : IProductEngine.HealthType.INITIAL;

require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
``` [2](#0-1) 

`liquidateSubaccountImpl` in `ClearinghouseLiq.sol` enforces the **MAINTENANCE** health check as the gate for liquidation:

```solidity
require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
``` [3](#0-2) 

`isUnderMaintenance` returns `true` only when maintenance health is strictly negative: [4](#0-3) 

When a subaccount's health satisfies:

```
INITIAL health  < 0   (blocks withdrawal)
MAINTENANCE health >= 0  (blocks liquidation)
```

both paths are simultaneously closed. The withdrawal reverts with `ERR_SUBACCT_HEALTH` and any liquidation attempt reverts with `ERR_NOT_LIQUIDATABLE`. No other on-chain path exists to reduce the subaccount's exposure or release its collateral.

---

### Impact Explanation

The user's deposited collateral is locked inside the protocol. They cannot withdraw any amount — even a partial withdrawal that would leave health unchanged — because the check is applied after the balance debit and any negative initial health causes a revert. No liquidator can intervene because the maintenance threshold has not been breached. The user is forced to either deposit additional collateral (which they may not have) or wait for an external price recovery. In volatile markets, the window between initial and maintenance health can persist for extended periods, effectively freezing user funds.

---

### Likelihood Explanation

This state is reachable through normal market operation. Any user holding a leveraged spot or perp position will cross the initial margin threshold before the maintenance threshold whenever prices move adversely. The gap between the two thresholds is a deliberate design parameter (`longWeightInitial` vs `longWeightMaintenance`), so the twilight zone is not a degenerate edge case — it is the normal pre-liquidation region that every at-risk subaccount passes through. No privileged access, governance action, or external dependency failure is required; ordinary price movement is sufficient.

---

### Recommendation

1. **Partial withdrawal to health floor**: Allow `withdrawCollateral` to succeed if the post-withdrawal initial health is exactly zero (i.e., the user withdraws only the surplus above the initial margin requirement), rather than reverting on any negative result.
2. **Maintenance-health withdrawal gate**: As a fallback, permit withdrawals when the subaccount is already under initial health but above maintenance health, capped at an amount that does not push maintenance health negative. This mirrors how Aave allows withdrawals up to the liquidation threshold.
3. **Forced-reduce path**: Introduce a permissioned "reduce-only" order type that the sequencer can execute for subaccounts in the twilight zone, allowing position reduction without a full liquidation.

---

### Proof of Concept

1. User deposits 1000 USDC and opens a 5× leveraged long perp position. Initial health = +200, maintenance health = +350.
2. Price drops 4%. Initial health = −10, maintenance health = +80.
3. User calls `WithdrawCollateral` for any amount → `Clearinghouse.withdrawCollateral` debits balance, calls `getHealth(sender, INITIAL)` → returns −10 (or worse after debit) → `require(...) >= 0` reverts with `ERR_SUBACCT_HEALTH`.
4. Liquidator calls `LiquidateSubaccount` → `liquidateSubaccountImpl` calls `isUnderMaintenance` → maintenance health = +80 ≥ 0 → reverts with `ERR_NOT_LIQUIDATABLE`.
5. User's 1000 USDC collateral is locked. No on-chain action can release it until price recovers or the user deposits additional margin. [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/libraries/RiskHelper.sol (L14-32)
```text
    struct RiskStore {
        // these weights are all
        // between 0 and 2
        // these integers are the real
        // weights times 1e9
        int32 longWeightInitial;
        int32 shortWeightInitial;
        int32 longWeightMaintenance;
        int32 shortWeightMaintenance;
        int128 priceX18;
    }

    struct Risk {
        int128 longWeightInitialX18;
        int128 shortWeightInitialX18;
        int128 longWeightMaintenanceX18;
        int128 shortWeightMaintenanceX18;
        int128 priceX18;
    }
```

**File:** core/contracts/Clearinghouse.sol (L408-419)
```text
        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
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

**File:** core/contracts/ClearinghouseLiq.sol (L598-607)
```text
    function liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn)
        external
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
        require(
            txn.liquidatee != X_ACCOUNT && txn.liquidatee != N_ACCOUNT,
            ERR_NOT_LIQUIDATABLE
        );
```
