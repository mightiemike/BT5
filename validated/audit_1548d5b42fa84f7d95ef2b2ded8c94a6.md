### Title
`burnNlp` Uses `MAINTENANCE` Health Check Instead of `INITIAL`, Allowing Intentional Under-Initial State — (`core/contracts/Clearinghouse.sol`)

---

### Summary

`burnNlp` enforces only a `MAINTENANCE` health check post-burn, while the symmetric `mintNlp` enforces `INITIAL`. This asymmetry allows a user to burn NLP tokens and leave their subaccount below initial health while remaining above maintenance health — a state the protocol normally prevents through all other balance-reducing operations.

---

### Finding Description

In `Clearinghouse.sol`, `mintNlp` and `burnNlp` are the two sides of the NLP lifecycle. `mintNlp` correctly enforces:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [1](#0-0) 

`burnNlp` enforces only:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [2](#0-1) 

The weight constraint enforced by `_addOrUpdateProduct` guarantees `longWeightInitial <= longWeightMaintenance`: [3](#0-2) 

When NLP has equal INITIAL and MAINTENANCE weights (e.g., both = 1, which satisfies the constraint), the net health change from a burn is identical for both health types:

```
ΔI = ΔM = quoteAmount − nlpAmount × price = −burnFee
```

The `burnFee` is `max(1, quoteAmount / 1000)`, with a minimum of `1` (one unit in the protocol's fixed-point representation). [4](#0-3) 

**Exploitable scenario:**

A subaccount holds NLP (equal weights) plus perp positions. The perp positions create a gap: `INITIAL_health = 0.5`, `MAINTENANCE_health = 50`. The user burns a small NLP amount, triggering `burnFee = 1`. Post-burn:

- `INITIAL_health = 0.5 − 1 = −0.5` → **below zero, not checked**
- `MAINTENANCE_health = 50 − 1 = 49` → passes the check at line 527

The transaction succeeds. The subaccount is now under-initial.

Liquidation requires `isUnderMaintenance`, which checks `MAINTENANCE < 0`: [5](#0-4) 

So the subaccount cannot be liquidated immediately. It sits in a limbo state: unable to open new positions, but also not liquidatable.

---

### Impact Explanation

A subaccount left under-initial but above maintenance has reduced margin buffer. Any subsequent adverse price move that pushes `MAINTENANCE < 0` triggers liquidation, but the gap between the actual position risk and the collected margin is smaller than the protocol intended. In extreme cases (fast price moves, illiquid markets), the insurance fund must absorb losses that the initial margin requirement was designed to prevent. The protocol's invariant — that all subaccounts either have `INITIAL >= 0` or are actively being liquidated — is broken.

---

### Likelihood Explanation

The precondition is realistic: any subaccount with NLP and perp positions where `INITIAL_health` is small but positive (less than 1 unit) and `MAINTENANCE_health` is larger. The minimum burn fee of `1` unit is the only trigger needed. The call path is fully on-chain and permissionless (user submits a `BurnNlp` transaction through the endpoint). No privileged access is required.

---

### Recommendation

Replace the `MAINTENANCE` health check in `burnNlp` with `INITIAL`, consistent with `mintNlp`, `forceRebalanceNlpPool`, and `nlpProfitShare`:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [6](#0-5) 

---

### Proof of Concept

**Setup:**
- NLP product: `longWeightInitial = longWeightMaintenance = 1e9` (equal weights, valid per constraint)
- Subaccount state before burn:
  - NLP balance: 10 units at price 1 → contributes 10 to both INITIAL and MAINTENANCE
  - Perp position: contributes −9.5 to INITIAL, −0.5 to MAINTENANCE (due to perp weight gap)
  - `INITIAL_health = 10 − 9.5 = 0.5`
  - `MAINTENANCE_health = 10 − 0.5 = 9.5`

**Action:** Submit `BurnNlp` with `nlpAmount = 1`:
- `quoteAmount = 1 × 1 − burnFee = 1 − 1 = 0` (burnFee = max(1, 1/1000) = 1)
- NLP balance: 9, Quote balance: +0
- `ΔI = 0 − 1×1×1 = −1`
- `ΔM = 0 − 1×1×1 = −1`

**Post-burn state:**
- `INITIAL_health = 0.5 − 1 = −0.5` → **negative, not checked**
- `MAINTENANCE_health = 9.5 − 1 = 8.5` → passes line 527 ✓

Transaction succeeds. Subaccount is under-initial. A price move of 8.5 units against the perp position pushes `MAINTENANCE < 0`, triggering liquidation with a margin buffer smaller than the protocol's initial margin requirement intended.

### Citations

**File:** core/contracts/Clearinghouse.sol (L479-482)
```text
        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/Clearinghouse.sol (L502-504)
```text
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
```

**File:** core/contracts/Clearinghouse.sol (L519-529)
```text
        require(
            spotEngine.getBalance(NLP_PRODUCT_ID, txn.sender).amount >= 0,
            ERR_SUBACCT_HEALTH
        );
        // Burning NLP can decrease health if the burn fee exceeds the health improvement
        // from the withdrawal. This check prevents malicious actors from deliberately
        // creating unhealthy subaccounts through NLP burns.
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
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

**File:** core/contracts/ClearinghouseLiq.sol (L603-603)
```text
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
```
