### Title
`burnNlp` Enforces Only `MAINTENANCE` Health Check, Allowing Subaccounts to Fall Below Initial Margin — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.burnNlp` checks only `MAINTENANCE` health after burning NLP tokens, while every other collateral-reducing operation (`withdrawCollateral`, `mintNlp`, `transferQuote`) enforces `INITIAL` health. A user can craft a small NLP burn where the flat minimum burn fee exceeds the health improvement from the weight difference, pushing their `INITIAL` health below zero while `MAINTENANCE` health remains positive. This violates the protocol's initial-margin invariant and erodes the safety buffer between initial and liquidation thresholds — the direct analog to the flash-claim undercollateralization class in the reference report.

---

### Finding Description

Three collateral-reducing operations in `Clearinghouse.sol` use `INITIAL` health:

- `withdrawCollateral` line 419: `require(getHealth(sender, IProductEngine.HealthType.INITIAL) >= 0, ERR_SUBACCT_HEALTH)` [1](#0-0) 

- `mintNlp` line 480: `require(getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0, ERR_SUBACCT_HEALTH)` [2](#0-1) 

- `transferQuote` line 249: `require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH)` [3](#0-2) 

`burnNlp` is the sole exception — it uses `MAINTENANCE`:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [4](#0-3) 

The developers acknowledge the risk in the comment immediately above this check:

> *"Burning NLP can decrease health if the burn fee exceeds the health improvement from the withdrawal."* [5](#0-4) 

The burn fee is `max(ONE, quoteAmount / 1000)` — a flat **$1 minimum** for any burn under $1000. [6](#0-5) 

`RiskHelper._getWeightX18` confirms that for long positions, `INITIAL` uses `longWeightInitialX18` and `MAINTENANCE` uses `longWeightMaintenanceX18`, where `longWeightMaintenanceX18 ≥ longWeightInitialX18` by construction (initial is always more conservative). [7](#0-6) 

**Concrete health arithmetic for a small burn:**

Suppose NLP `longWeightInitialX18 = 0.99` and `longWeightMaintenanceX18 = 0.995`. A user burns $10 of NLP:

| | Formula | Value |
|---|---|---|
| Quote received | $10 − $1 fee | $9 |
| Initial health Δ | $9 × 1.0 − $10 × 0.99 | **−$0.90** |
| Maintenance health Δ | $9 × 1.0 − $10 × 0.995 | **−$0.95** |

Both decrease, but the **level** of maintenance health can absorb the drop while initial health cannot. If the user is positioned at initial health = +$0.50 and maintenance health = +$5.50:

- After burn: initial health = **−$0.40** (below zero — would fail `INITIAL` check)
- After burn: maintenance health = **+$4.55** (passes `MAINTENANCE` check)

The `burnNlp` call succeeds, leaving the subaccount below initial margin.

The attacker-controlled entry path is:
1. User signs a `BurnNlp` transaction with a chosen `nlpAmount`
2. Sequencer submits via `submitTransactionsChecked()` → `Endpoint` → `delegatecall` → `EndpointTx.processTransactionImpl`
3. `EndpointTx` dispatches to `clearinghouse.burnNlp` [8](#0-7) 

No privilege is required. The user controls `nlpAmount` and can tune it to the exact amount that crosses the initial-health boundary while staying above maintenance.

---

### Impact Explanation

The corrupted state: `INITIAL` health < 0 while `MAINTENANCE` health ≥ 0. This state is unreachable through any other user-facing operation. Concretely:

1. **Invariant broken**: the protocol's invariant that every non-liquidatable account satisfies initial margin is violated.
2. **Safety buffer eroded**: the gap between initial and maintenance margin — the protocol's first line of defense against bad debt — is consumed. A small adverse price move now pushes the account directly into liquidation range with no buffer.
3. **Bad debt risk**: if the price moves before the sequencer can liquidate, the account becomes insolvent and the protocol absorbs the loss.
4. **Corrupted balance**: the specific corrupted value is the subaccount's `INITIAL` health, which is now negative while the on-chain state records no liquidatable condition.

---

###

### Citations

**File:** core/contracts/Clearinghouse.sol (L247-250)
```text
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -toTransfer);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.recipient, toTransfer);
        require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
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

**File:** core/contracts/Clearinghouse.sol (L503-504)
```text
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

**File:** core/contracts/libraries/RiskHelper.sol (L44-55)
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
