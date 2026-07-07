### Title
`burnNlp` Uses `MAINTENANCE` Health Check Instead of `INITIAL`, Allowing Subaccount to Drop Below Initial Health — (`core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.burnNlp` enforces only a `MAINTENANCE` health check after reducing a user's NLP balance and crediting quote. Every other collateral-reducing path in the same contract (`withdrawCollateral`, `mintNlp`, `nlpProfitShare`, `forceRebalanceNlpPool`) enforces an `INITIAL` health check. A user with a large perp position — where INITIAL health is small but positive and MAINTENANCE health is substantially higher — can burn NLP, pay the burn fee, and leave their subaccount below INITIAL health while the MAINTENANCE check passes.

---

### Finding Description

In `Clearinghouse.burnNlp`, after removing NLP and crediting quote (minus a burn fee), the only post-state health guard is:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [1](#0-0) 

Compare with `mintNlp`, which correctly uses `INITIAL`:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [2](#0-1) 

And `withdrawCollateral`, which also uses `INITIAL` for all non-X_ACCOUNT senders:

```solidity
IProductEngine.HealthType healthType = sender == X_ACCOUNT
    ? IProductEngine.HealthType.PNL
    : IProductEngine.HealthType.INITIAL;
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
``` [3](#0-2) 

The burn fee is computed as `max(ONE, quoteAmount / 1000)`, so the minimum fee is `ONE` (1 USDC in 18-decimal fixed-point):

```solidity
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
``` [4](#0-3) 

The burn fee is a net loss to the subaccount. It reduces the quote balance, which has weight `1.0` for both INITIAL and MAINTENANCE. Therefore, the burn fee decreases both INITIAL and MAINTENANCE health by the same amount.

A subaccount holding a large short perp position has INITIAL health < MAINTENANCE health, because short perp positions carry a higher penalty under INITIAL weights (`shortWeightInitial >= shortWeightMaintenance` is enforced by `_addOrUpdateProduct`):

```solidity
require(
    riskStore.longWeightInitial <= riskStore.longWeightMaintenance &&
        riskStore.longWeightMaintenance <= 10**9 &&
        riskStore.shortWeightInitial >=
        riskStore.shortWeightMaintenance &&
        riskStore.shortWeightMaintenance >= 10**9,
    ERR_BAD_PRODUCT_CONFIG
);
``` [5](#0-4) 

This creates a gap: INITIAL health can be small and positive while MAINTENANCE health is large and positive. If the burn fee exceeds the user's INITIAL health, INITIAL health goes negative while MAINTENANCE health remains positive — and the check at line 527 passes.

---

### Impact Explanation

The user extracts quote collateral via NLP burn while leaving their subaccount below INITIAL health. This violates the protocol invariant that any collateral-reducing action must leave the subaccount at or above INITIAL health. The resulting state enables the user to hold open perp positions that are undercollateralized at the INITIAL level, which is the threshold the protocol uses to gate new position-opening and collateral withdrawal. This is a direct unauthorized mutation of the subaccount's collateral state beyond what the risk model permits.

---

### Likelihood Explanation

The precondition is reachable in normal trading: a user opens a large short perp position, bringing their INITIAL health close to zero while MAINTENANCE health remains comfortably positive. The minimum burn fee is 1 USDC (the `ONE` constant), so any user whose INITIAL health is between 0 and 1 USDC can trigger this with the smallest possible NLP burn. No special privileges, admin access, or external dependencies are required — only a signed `BurnNlp` transaction submitted through the standard endpoint flow.

---

### Recommendation

Replace the `MAINTENANCE` health check in `burnNlp` with `INITIAL`, consistent with every other collateral-reducing function in `Clearinghouse.sol`:

```solidity
// Before (line 527):
getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,

// After:
getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
``` [1](#0-0) 

---

### Proof of Concept

1. Deploy the protocol on a local Hardhat fork.
2. Create a subaccount. Open a large short perp position such that:
   - `getHealth(sender, INITIAL)` = `+0.5e18` (0.5 USDC)
   - `getHealth(sender, MAINTENANCE)` = `+10e18` (10 USDC)
3. Mint a small amount of NLP (e.g., worth 1000 USDC quote).
4. Submit a `BurnNlp` transaction for that NLP amount.
   - `burnFee = max(ONE, 1000e18 / 1000) = 1e18` (1 USDC)
   - Net quote credited = 999 USDC
   - Both INITIAL and MAINTENANCE health decrease by 1 USDC (the fee is a pure loss in quote)
5. After the transaction:
   - `getHealth(sender, INITIAL)` = `0.5 - 1 = -0.5e18` → **negative**
   - `getHealth(sender, MAINTENANCE)` = `10 - 1 = 9e18` → positive, check passes
6. Assert `getHealth(sender, INITIAL) < 0` — the invariant is broken.

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

**File:** core/contracts/Clearinghouse.sol (L503-504)
```text
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
```

**File:** core/contracts/Clearinghouse.sol (L526-529)
```text
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
