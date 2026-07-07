### Title
`burnNlp` Uses Weaker `MAINTENANCE` Health Check, Allowing Users to Bypass the `INITIAL` Health Invariant — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

Every user-controlled operation in `Clearinghouse.sol` that reduces collateral or increases risk enforces `HealthType.INITIAL` after the state change. `burnNlp` is the sole exception: it enforces only `HealthType.MAINTENANCE`. Because MAINTENANCE weights are strictly more lenient than INITIAL weights, a user can burn NLP and land in a state where `INITIAL health < 0` while `MAINTENANCE health >= 0`, bypassing the protocol's primary safety invariant.

---

### Finding Description

`RiskHelper._getWeightX18` returns different weights depending on `HealthType`:

- `INITIAL` → `longWeightInitialX18` / `shortWeightInitialX18` (conservative, lower leverage)
- `MAINTENANCE` → `longWeightMaintenanceX18` / `shortWeightMaintenanceX18` (lenient, higher leverage) [1](#0-0) 

Because maintenance weights are always ≥ initial weights, `getHealth(sub, MAINTENANCE) >= getHealth(sub, INITIAL)` always holds. A subaccount can therefore satisfy the MAINTENANCE check while failing the INITIAL check.

Every collateral-reducing or risk-increasing operation enforces INITIAL health:

| Operation | Health check used |
|---|---|
| `withdrawCollateral` | `INITIAL` (line 417) |
| `mintNlp` | `INITIAL` (line 480) |
| `transferQuote` | `INITIAL` via `_isAboveInitial` (line 249) |
| **`burnNlp`** | **`MAINTENANCE` (line 527)** | [2](#0-1) [3](#0-2) [4](#0-3) 

`burnNlp` removes NLP from the user and credits quote minus a burn fee:

```solidity
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
``` [5](#0-4) 

The net INITIAL health change from burning `nlpAmount` NLP at price `P` with initial weight `w` is:

```
ΔH_initial = (nlpAmount·P − burnFee)·quoteWeight − nlpAmount·P·w
           = nlpAmount·P·(1 − w) − burnFee
```

Since `quoteWeight = 1` and `burnFee ≈ nlpAmount·P / 1000`, if `w > 0.999` (i.e., NLP's initial weight is close to 1), the term `nlpAmount·P·(1 − w)` is smaller than `burnFee`, making `ΔH_initial < 0`. The code's own comment acknowledges this: *"Burning NLP can decrease health if the burn fee exceeds the health improvement from the withdrawal."* [6](#0-5) 

---

### Impact Explanation

A user whose INITIAL health is in the range `(0, burnFee)` can call `burnNlp` and exit with `INITIAL health < 0`. This:

1. **Violates the INITIAL health invariant** — the protocol guarantees that no user-controlled action leaves a subaccount below INITIAL health. `burnNlp` breaks this guarantee.
2. **Erodes the liquidation buffer** — the gap between INITIAL and MAINTENANCE health is the protocol's early-warning margin. A subaccount with `INITIAL health < 0` but `MAINTENANCE health >= 0` is not liquidatable (`isUnderMaintenance` returns false), yet it is already past the safety threshold. [7](#0-6) 

3. **Systematic risk** — if prices move adversely even slightly, the subaccount crosses the MAINTENANCE threshold and becomes liquidatable with no buffer remaining, increasing the risk of bad debt for the protocol.

---

### Likelihood Explanation

`burnNlp` is a standard user-facing operation reachable through the `Endpoint` by any trader who holds NLP tokens. A user with a leveraged position and INITIAL health just above zero — a common state for active traders — can trigger this condition by burning any non-trivial NLP amount. No special privileges, flash loans, or external dependencies are required.

---

### Recommendation

Replace `HealthType.MAINTENANCE` with `HealthType.INITIAL` in `burnNlp`:

```solidity
// Before (vulnerable):
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);

// After (fixed):
require(
    getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [4](#0-3) 

This aligns `burnNlp` with every other collateral-reducing operation in the contract.

---

### Proof of Concept

1. Assume NLP has `longWeightInitial = 0.9995e18` (close to 1) and `longWeightMaintenance = 0.9998e18`.
2. User holds a leveraged spot position; their INITIAL health = `0.4 USDC` and MAINTENANCE health = `2 USDC`.
3. User also holds NLP worth `1000 USDC` (minted earlier when health was healthy).
4. User calls `burnNlp` with `nlpAmount` corresponding to `1000 USDC`:
   - `burnFee = max(1, 1000/1000) = 1 USDC`
   - `quoteReceived = 999 USDC`
   - `ΔH_initial = 999·1 − 1000·0.9995 = 999 − 999.5 = −0.5 USDC`
5. Post-burn INITIAL health = `0.4 − 0.5 = −0.1 USDC` (negative — invariant broken).
6. Post-burn MAINTENANCE health = `2 + 999 − 1000·0.9998 = 2 + 999 − 999.8 = 1.2 USDC` (positive — MAINTENANCE check passes).
7. `burnNlp` succeeds. The subaccount now has `INITIAL health < 0` with no way for the protocol to liquidate it until MAINTENANCE health also turns negative. [8](#0-7)

### Citations

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

**File:** core/contracts/Clearinghouse.sol (L485-530)
```text
    function burnNlp(
        IEndpoint.BurnNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.nlpAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 nlpAmount = int128(txn.nlpAmount);
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, -quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] <= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);

        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
        }

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
