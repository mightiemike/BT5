### Title
Wrong `HealthType` Used in `burnNlp` Allows Bypassing Initial Health Check — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.burnNlp` enforces a post-operation health check using `HealthType.MAINTENANCE`, while the analogous `withdrawCollateral` enforces `HealthType.INITIAL`. Because `MAINTENANCE` health is always ≥ `INITIAL` health (maintenance weights are less conservative), a user whose `INITIAL` health is negative but whose `MAINTENANCE` health is positive can burn NLP tokens and receive quote — effectively withdrawing value — while bypassing the stricter `INITIAL` health invariant the protocol enforces everywhere else.

---

### Finding Description

In `Clearinghouse.sol`, two operations that reduce a user's collateral value use different health thresholds:

`withdrawCollateral` (line 415–417) enforces `HealthType.INITIAL`:

```solidity
IProductEngine.HealthType healthType = sender == X_ACCOUNT
    ? IProductEngine.HealthType.PNL
    : IProductEngine.HealthType.INITIAL;
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
``` [1](#0-0) 

`burnNlp` (line 526–529) enforces only `HealthType.MAINTENANCE`:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [2](#0-1) 

`mintNlp`, the symmetric deposit-side operation, correctly uses `HealthType.INITIAL`:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [3](#0-2) 

`RiskHelper._getWeightX18` shows that `MAINTENANCE` uses `longWeightMaintenanceX18` / `shortWeightMaintenanceX18`, which are less conservative than the `INITIAL` counterparts, so `MAINTENANCE` health ≥ `INITIAL` health for all positions: [4](#0-3) 

The burn path is reachable by any user: they sign a `BurnNlp` transaction, the sequencer submits it via `EndpointTx.processTransactionImpl`, which calls `clearinghouse.burnNlp`. [5](#0-4) 

---

### Impact Explanation

A user with a leveraged perp position whose `INITIAL` health is negative (e.g., −10) but whose `MAINTENANCE` health is positive (e.g., +50) is blocked from calling `withdrawCollateral`. However, they can call `burnNlp` to convert NLP tokens into quote, receiving real value despite being below the `INITIAL` health threshold. This is a direct bypass of the withdrawal health invariant: the user extracts collateral value through the NLP burn path that the protocol explicitly forbids through the standard withdrawal path. Repeated use can progressively deepen the `INITIAL` health deficit while keeping `MAINTENANCE` health positive, leaving the protocol holding undercollateralized risk.

---

### Likelihood Explanation

Any user who holds NLP tokens and has a leveraged position can reach this state through normal market price movement. No privileged access, governance action, or external dependency is required. The `BurnNlp` transaction is a standard signed user transaction processed by the sequencer. The condition (`INITIAL` health < 0, `MAINTENANCE` health ≥ 0) is a routine intermediate state for leveraged accounts during volatility.

---

### Recommendation

Change `HealthType.MAINTENANCE` to `HealthType.INITIAL` in `burnNlp`, consistent with `withdrawCollateral` and `mintNlp`:

```diff
- require(
-     getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
-     ERR_SUBACCT_HEALTH
- );
+ require(
+     getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
+     ERR_SUBACCT_HEALTH
+ );
``` [6](#0-5) 

---

### Proof of Concept

1. User opens a leveraged perp position. Price moves adversely; their `INITIAL` health drops to −5, `MAINTENANCE` health remains +80.
2. User attempts `withdrawCollateral` — reverts because `INITIAL` health < 0.
3. User holds NLP tokens. They submit a signed `BurnNlp` transaction for their full NLP balance.
4. `burnNlp` executes: NLP balance is zeroed, quote balance increases by `nlpAmount * oraclePriceX18 - burnFee`.
5. Post-burn health check: `getHealth(sender, MAINTENANCE) >= 0` — passes.
6. The `INITIAL` health check that would have blocked this operation is never performed.
7. User has successfully withdrawn value (quote received from NLP burn) despite having negative `INITIAL` health — the same outcome that `withdrawCollateral` explicitly prevents.

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

**File:** core/contracts/EndpointTx.sol (L554-573)
```text
        } else if (txType == IEndpoint.TransactionType.BurnNlp) {
            IEndpoint.SignedBurnNlp memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedBurnNlp)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.burnNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
```
