### Title
`burnNlp` Enforces Only Maintenance Health, Allowing Post-Burn Initial Health Violation — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

`burnNlp` in `Clearinghouse.sol` performs a post-operation health check using `HealthType.MAINTENANCE` instead of `HealthType.INITIAL`. This is directly analogous to the MEV lane underestimating block size by not counting bundled transactions: in both cases, a weaker bound is checked while the binding protocol limit goes unverified. A user can burn NLP tokens and exit with `INITIAL` health below zero — a state the protocol explicitly prohibits for all other collateral-reducing operations — while remaining above the maintenance threshold and therefore not liquidatable.

---

### Finding Description

Every other collateral-reducing path in the Clearinghouse enforces `INITIAL` health as the post-operation invariant:

- `withdrawCollateral` (line 419): `require(getHealth(sender, HealthType.INITIAL) >= 0)`
- `transferQuote` (line 249): `require(_isAboveInitial(txn.sender))`
- `mintNlp` (line 480): `require(getHealth(txn.sender, HealthType.INITIAL) >= 0)`

`burnNlp` is the sole exception:

```solidity
// core/contracts/Clearinghouse.sol  lines 526-529
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
```

`INITIAL` health uses stricter risk weights (lower long-weight, higher short-weight) than `MAINTENANCE` health. The gap between the two thresholds is the protocol's safety buffer — the margin of leverage that is permitted for existing positions but not for new ones. By checking only `MAINTENANCE`, `burnNlp` allows a user to consume that entire buffer in a single operation, leaving the subaccount in a state that no other entry point would permit.

The in-code comment acknowledges the asymmetry ("Burning NLP can decrease health if the burn fee exceeds the health improvement from the withdrawal") but resolves it with the weaker check rather than the correct one. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

After a successful `burnNlp` call, the sender's subaccount can hold `INITIAL` health `< 0` while `MAINTENANCE` health `>= 0`. In this state:

1. **Over-leverage is silently accepted.** The subaccount carries more risk exposure than the protocol's initial-margin rules allow. The insurance fund and NLP pool bear the tail risk of this excess leverage.
2. **The subaccount is not liquidatable.** Because `MAINTENANCE` health is non-negative, no liquidator can act. The position sits in a limbo state that the protocol's risk model was not designed to handle.
3. **Subsequent adverse price movement converts the excess leverage directly into a liquidation shortfall.** Because the subaccount entered the danger zone with no margin buffer, any move against the position immediately threatens the insurance fund.

The corrupted invariant is: `INITIAL health >= 0` for any subaccount that has not been flagged for liquidation. The corrupted state variable is the subaccount's effective leverage ratio as tracked across `SpotEngine` and `PerpEngine` balances. [4](#0-3) 

---

### Likelihood Explanation

The trigger is straightforward and requires no special privilege:

- A user holds NLP tokens **and** has open leveraged positions (perp or borrowed spot) such that their `INITIAL` health is positive but close to zero.
- The user submits a `BurnNlp` transaction for a large enough `nlpAmount` that the NLP collateral removed exceeds the quote received (after the burn fee), causing a net decrease in `INITIAL` health.
- Because NLP tokens carry an `INITIAL` long-weight and the burn fee is non-trivial (`max(1e18, quoteAmount / 1000)`), the health decrease is real and controllable by the user through choice of `nlpAmount`.

The user controls `txn.nlpAmount` directly. The sequencer controls `nlpPoolRebalanceX18`, but the health impact on the *sender* depends only on the NLP balance removed and the quote credited — both determined by `nlpAmount` and the oracle price, not the rebalance vector. [5](#0-4) 

---

### Recommendation

Replace the `MAINTENANCE` health check in `burnNlp` with an `INITIAL` health check, consistent with every other collateral-reducing operation:

```solidity
// Replace:
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

If the intent is to allow burning NLP even when it slightly reduces `INITIAL` health (e.g., to let users exit NLP positions when already near the initial margin boundary), the correct approach is to gate on `INITIAL` health and document the explicit exception, not to silently fall back to the weaker `MAINTENANCE` check. [6](#0-5) 

---

### Proof of Concept

**Setup:**
- Alice holds 100 NLP tokens (worth 10,000 USDC at oracle price 100).
- Alice also holds a large short perp position such that her `INITIAL` health is +500 USDC and her `MAINTENANCE` health is +2,000 USDC (the gap reflects the stricter initial weights).

**Attack:**
1. Alice submits `BurnNlp` with `nlpAmount` = 100.
2. `quoteAmount = 100 * 100 = 10,000`. `burnFee = max(1e18, 10,000/1000) = 10`. Net quote credited = 9,990.
3. NLP collateral removed reduces `INITIAL` health by, say, 1,200 USDC (NLP carries a higher initial weight than raw quote). Quote credited adds back 9,990 * quote_initial_weight. Net `INITIAL` health delta = −700 USDC.
4. Post-burn: `INITIAL` health = 500 − 700 = **−200 USDC** (below zero). `MAINTENANCE` health = 2,000 − 300 = **+1,700 USDC** (above zero).
5. The `require(getHealth(..., MAINTENANCE) >= 0)` check at line 527 **passes**. The transaction succeeds.
6. Alice's subaccount is now over-leveraged beyond initial-margin limits, is not liquidatable, and the protocol's risk model is violated. [7](#0-6) [8](#0-7)

### Citations

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
