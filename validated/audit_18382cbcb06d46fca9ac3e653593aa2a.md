### Title
`burnNlp` Uses `MAINTENANCE` Health Type for Post-Burn Safety Check Instead of `INITIAL`, Allowing Users to Create Undercollateralized Positions — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`burnNlp` enforces a post-burn health check using `HealthType.MAINTENANCE` rather than `HealthType.INITIAL`. Because `MAINTENANCE` health is always ≥ `INITIAL` health (maintenance weights are strictly less conservative), a user whose `INITIAL` health would be negative after the burn can still pass the check. This is the direct analog of the reported bug: a less conservative metric is used for a safety gate that should use the most conservative metric, allowing an operation that should be blocked.

---

### Finding Description

In `Clearinghouse.sol`, `burnNlp` reduces the sender's NLP balance and credits quote tokens. After the state change, it performs a health check:

```solidity
// core/contracts/Clearinghouse.sol lines 526-529
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
```

Every other user-initiated operation that modifies balances uses `INITIAL` health for the post-operation check:

- `withdrawCollateral` (line 417–419): uses `HealthType.INITIAL` for non-X_ACCOUNT senders
- `mintNlp` (line 479–482): uses `HealthType.INITIAL`
- `forceRebalanceNlpPool` (line 541–548): uses `HealthType.INITIAL`
- `nlpProfitShare` (line 563–566): uses `HealthType.INITIAL`

The weight ordering is enforced at product registration in `BaseEngine._addOrUpdateProduct` (lines 236–241):

```solidity
riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
    riskStore.longWeightMaintenance <= 10**9 &&
    riskStore.shortWeightInitial >= riskStore.shortWeightMaintenance &&
    riskStore.shortWeightMaintenance >= 10**9
```

This guarantees `MAINTENANCE health ≥ INITIAL health` for every subaccount. Consequently, the `burnNlp` check passes whenever `MAINTENANCE health ≥ 0`, even if `INITIAL health < 0`.

---

### Impact Explanation

**Impact: Medium**

A user can burn NLP tokens and receive quote collateral while leaving their subaccount in a state where `INITIAL health < 0`. This violates the protocol's core invariant that user-initiated operations must not push a subaccount below initial margin. The resulting undercollateralized position:

- Cannot be opened via any other path (all other operations enforce `INITIAL` health)
- Exposes the protocol to losses if prices move adversely before the position is liquidated (liquidation is only triggered at `MAINTENANCE health < 0`, which is a weaker threshold)
- Allows a user to extract quote value from NLP while holding a position that the protocol would otherwise reject

---

### Likelihood Explanation

**Likelihood: Medium**

Any user holding NLP tokens whose subaccount has `INITIAL health < 0 ≤ MAINTENANCE health` can exploit this. This condition arises naturally when a user's collateral ratio sits between the initial and maintenance margin bands — a common scenario during moderate market moves. The user submits a `BurnNlp` transaction through the standard endpoint path; no privileged access is required.

---

### Recommendation

Replace `HealthType.MAINTENANCE` with `HealthType.INITIAL` in the `burnNlp` post-burn health check, consistent with every other user-facing balance-modifying operation:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
    ERR_SUBACCT_HEALTH
);
```

---

### Proof of Concept

1. User holds a perp long position. After a moderate price drop, their subaccount has:
   - `INITIAL health = -5` (below initial margin)
   - `MAINTENANCE health = +3` (above maintenance margin, so not yet liquidatable)
2. User also holds NLP tokens acquired earlier.
3. User submits a `BurnNlp` transaction via the endpoint.
4. `burnNlp` executes: NLP balance decreases, quote balance increases.
5. Post-burn check: `getHealth(sender, MAINTENANCE) >= 0` → passes (MAINTENANCE health is still ≥ 0 after receiving quote).
6. The subaccount now holds a position with `INITIAL health < 0` — a state that `withdrawCollateral`, `mintNlp`, and all other operations would have rejected.
7. The user has extracted quote value while leaving the protocol holding an undercollateralized position that only becomes liquidatable if prices fall further to breach the maintenance threshold.

**Root cause:** [1](#0-0) 

**Inconsistency with `mintNlp`:** [2](#0-1) 

**Inconsistency with `withdrawCollateral`:** [3](#0-2) 

**Weight ordering invariant (INITIAL ≤ MAINTENANCE):** [4](#0-3)

### Citations

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/Clearinghouse.sol (L479-482)
```text
        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/Clearinghouse.sol (L526-529)
```text
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/BaseEngine.sol (L236-241)
```text
            riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
                riskStore.longWeightMaintenance <= 10**9 &&
                riskStore.shortWeightInitial >=
                riskStore.shortWeightMaintenance &&
                riskStore.shortWeightMaintenance >= 10**9,
            ERR_BAD_PRODUCT_CONFIG
```
