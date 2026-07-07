### Title
Inconsistent Health Check Type in `burnNlp` vs `mintNlp` Allows Bypassing Initial Health Requirement — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

`mintNlp` enforces an `INITIAL` health check after minting NLP tokens, but `burnNlp` only enforces a `MAINTENANCE` health check after burning. This inconsistency — mirroring the Velodrome `balanceOfNFT` / `balanceOfNFTAt` pattern — allows a user to deliberately burn NLP and leave their subaccount with initial health below zero, bypassing the protocol's standard risk buffer requirement that every other user-initiated collateral-reducing action enforces.

---

### Finding Description

In `Clearinghouse.sol`, the two symmetric NLP operations apply different health check types at their post-condition:

**`mintNlp`** (line 479–482):
```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [1](#0-0) 

**`burnNlp`** (line 526–529):
```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [2](#0-1) 

Every other user-initiated action that reduces a subaccount's collateral or health uses `INITIAL`:

- `withdrawCollateral` (line 419): `getHealth(sender, healthType) >= 0` where `healthType = INITIAL` for non-X accounts. [3](#0-2) 

- `transferQuote` (line 249): `require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH)`. [4](#0-3) 

- `mintNlp` (line 480): `INITIAL`. [1](#0-0) 

`burnNlp` is the sole outlier. The in-code comment acknowledges the intent ("prevents malicious actors from deliberately creating unhealthy subaccounts") but the chosen threshold — `MAINTENANCE` — only prevents the subaccount from becoming immediately liquidatable, not from violating the initial health buffer that the rest of the protocol enforces.

The `INITIAL` health type requires a larger collateral buffer than `MAINTENANCE`. A subaccount can satisfy `MAINTENANCE >= 0` while simultaneously having `INITIAL < 0`. The gap between the two thresholds is the exploitable window.

The entry path is user-controlled: a user signs a `BurnNlp` transaction, the sequencer includes it in a batch via `submitTransactionsChecked`, which calls `processTransactionImpl` → `clearinghouse.burnNlp`. [5](#0-4) 

The burn fee (`burnFee = MathHelper.max(ONE, quoteAmount / 1000)`) is deducted from the quote received, meaning the net health effect of a burn can be negative — the user loses more in health than they gain from the returned quote. This is precisely the mechanism that can push `INITIAL` health below zero while keeping `MAINTENANCE` health non-negative. [6](#0-5) 

---

### Impact Explanation

A user whose subaccount has `INITIAL` health slightly above zero can burn NLP tokens such that the burn fee pushes `INITIAL` health below zero while `MAINTENANCE` health remains non-negative. After the call succeeds:

- The subaccount holds `INITIAL` health < 0, a state that `withdrawCollateral`, `transferQuote`, and `mintNlp` would all reject.
- The user has effectively bypassed the protocol's risk buffer requirement, operating closer to the liquidation threshold than the risk parameters permit.
- In volatile market conditions, the reduced buffer increases the probability of the subaccount crossing the `MAINTENANCE` threshold and being liquidated, potentially at a loss to the insurance fund if the liquidation is not fully covered.

**Impact: Medium** — the subaccount is not immediately insolvent, but the protocol's intended risk margin is silently violated, degrading the solvency guarantee the initial health check is designed to provide.

---

### Likelihood Explanation

**Likelihood: Medium.** The condition requires:
1. The user holds unlocked NLP tokens (a normal state for any NLP participant).
2. The user's `INITIAL` health is in the range `(0, burnFee_health_equivalent)` — a realistic scenario given that NLP positions are actively managed and health fluctuates with prices.
3. The user deliberately chooses a burn amount that maximises the fee impact relative to their health buffer.

No privileged access, oracle manipulation, or external dependency is required. The user controls the `nlpAmount` field in the signed `BurnNlp` transaction.

---

### Recommendation

Replace the `MAINTENANCE` health check in `burnNlp` with an `INITIAL` health check, consistent with `mintNlp` and all other user-initiated collateral-reducing operations:

```solidity
// In burnNlp, replace:
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);

// With:
require(
    getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
    ERR_SUBACCT_HEALTH
);
```

If the protocol intentionally allows burns that reduce initial health (e.g., to avoid trapping users with locked NLP), the asymmetry should be explicitly documented and the risk accepted with a clear rationale, not silently diverge from the invariant enforced everywhere else.

---

### Proof of Concept

1. User subaccount has: 100 USDC collateral, 10 NLP tokens (unlocked), open perp position. `INITIAL` health = +2, `MAINTENANCE` health = +10.
2. User signs `BurnNlp { nlpAmount = 10 }`. Sequencer includes it.
3. `burnNlp` executes: `quoteAmount = 10 * oraclePrice`, `burnFee = max(1e18, quoteAmount/1000)`. Suppose `burnFee` in health terms = 5 units.
4. After balance updates: `INITIAL` health = 2 − 5 = **−3**, `MAINTENANCE` health = 10 − 5 = **+5**.
5. The check `getHealth(txn.sender, MAINTENANCE) >= 0` passes (5 >= 0). Transaction succeeds.
6. Subaccount now has `INITIAL` health = −3. Calling `withdrawCollateral` or `transferQuote` would revert with `ERR_SUBACCT_HEALTH`, but the subaccount reached this state through `burnNlp` without any such rejection.
7. The subaccount is now operating with a negative initial health buffer, violating the protocol's risk invariant. [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/Clearinghouse.sol (L249-249)
```text
        require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/Clearinghouse.sol (L453-483)
```text
    function mintNlp(
        IEndpoint.MintNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.quoteAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] >= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);

        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
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
