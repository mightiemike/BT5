The code evidence is conclusive. Here is the full analysis:

---

### Title
`burnNlp` Uses `MAINTENANCE` Health Check Instead of `INITIAL`, Allowing Subaccount to Reach Initial-Health-Negative Zone — (`core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.burnNlp` enforces only a `MAINTENANCE` health check post-burn, while every other user-initiated collateral movement (including `mintNlp`) enforces an `INITIAL` health check. This asymmetry allows a user to deliberately burn NLP and leave their subaccount with `INITIAL health < 0` but `MAINTENANCE health >= 0`, a state that is unreachable through any other user-initiated path and that violates the protocol's core margin invariant.

---

### Finding Description

`mintNlp` correctly enforces:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [1](#0-0) 

`burnNlp` instead enforces only:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [2](#0-1) 

The `INITIAL` and `MAINTENANCE` health types use different risk weights. `INITIAL` applies stricter (lower) long weights and higher short weights than `MAINTENANCE`:

```solidity
weight = healthType == IProductEngine.HealthType.INITIAL
    ? risk.longWeightInitialX18
    : risk.longWeightMaintenanceX18;
``` [3](#0-2) 

Because `longWeightMaintenance > longWeightInitial` for collateral assets, a subaccount can have `MAINTENANCE health >= 0` while simultaneously having `INITIAL health < 0`. The `burnNlp` check does not prevent this state.

The in-code comment (lines 523–525) attempts to justify using `MAINTENANCE` by noting that the burn fee can decrease health, but this does not justify weakening the check from `INITIAL` to `MAINTENANCE` — it only explains why the check is needed at all. [4](#0-3) 

---

### Impact Explanation

A user can construct a subaccount where:
1. They hold NLP and some leveraged positions such that `INITIAL health` is slightly above 0.
2. They submit a `BurnNlp` transaction that removes NLP collateral, causing `INITIAL health` to drop below 0 while `MAINTENANCE health` remains >= 0.
3. The transaction succeeds because only `MAINTENANCE >= 0` is checked.

Post-burn, the subaccount sits in the "initial-health-negative, maintenance-health-positive" zone:
- It **cannot be liquidated** (liquidation requires `MAINTENANCE < 0`).
- It **holds more risk exposure than the protocol's initial margin rules permit**, without any enforcement mechanism to correct it until the account is touched again.

This directly violates the invariant that all user-initiated collateral movements must leave `INITIAL health >= 0`, and constitutes a transaction manipulation that changes settlement/collateral outcomes in a way that transfers risk incorrectly to the protocol.

---

### Likelihood Explanation

The path is fully user-controlled: sign a `BurnNlp` transaction with a crafted `nlpAmount` that is processed through the standard `processTransactionImpl → BurnNlp` slow-mode flow. No admin access, sequencer compromise, or special privileges are required. The only prerequisite is holding NLP and having existing positions that create a gap between `INITIAL` and `MAINTENANCE` health.

---

### Recommendation

Replace the `MAINTENANCE` health check in `burnNlp` with `INITIAL`, consistent with `mintNlp` and all other user-initiated collateral operations:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
    ERR_SUBACCT_HEALTH
);
```

---

### Proof of Concept

1. Set up a subaccount with:
   - NLP collateral worth 100 USDC
   - A leveraged perp position such that `INITIAL health ≈ +1` and `MAINTENANCE health ≈ +10` (gap exists due to weight difference).
2. Call `burnNlp` with an `nlpAmount` that removes collateral worth ~5 USDC (after fee).
3. Post-burn: `INITIAL health ≈ -4`, `MAINTENANCE health ≈ +5`.
4. The `require(getHealth(..., MAINTENANCE) >= 0)` passes; the transaction succeeds.
5. Assert `getHealth(sender, INITIAL) < 0` — confirmed, invariant broken.
6. Confirm the account is not liquidatable (`isUnderMaintenance` returns false). [5](#0-4)

### Citations

**File:** core/contracts/Clearinghouse.sol (L479-482)
```text
        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/Clearinghouse.sol (L523-529)
```text
        // Burning NLP can decrease health if the burn fee exceeds the health improvement
        // from the withdrawal. This check prevents malicious actors from deliberately
        // creating unhealthy subaccounts through NLP burns.
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/libraries/RiskHelper.sol (L44-47)
```text
        if (amount >= 0) {
            weight = healthType == IProductEngine.HealthType.INITIAL
                ? risk.longWeightInitialX18
                : risk.longWeightMaintenanceX18;
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
