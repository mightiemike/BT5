### Title
`burnNlp` Applies a Burn Fee That Can Drive INITIAL Health Below Zero Without Reverting — (`core/contracts/Clearinghouse.sol`)

---

### Summary

`burnNlp()` in `Clearinghouse.sol` applies a `burnFee` that reduces the quote amount credited to the caller, but only validates `MAINTENANCE` health post-operation. Every other collateral-affecting function (`mintNlp`, `withdrawCollateral`, `transferQuote`) validates `INITIAL` health. The burn fee can push a subaccount's `INITIAL` health below zero while `MAINTENANCE` health remains non-negative, creating a protocol-inconsistent state that the contract accepts without reverting.

---

### Finding Description

In `burnNlp`, the protocol computes a burn fee and deducts it from the quote the user receives:

```solidity
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
``` [1](#0-0) 

After applying the balance changes, the only post-operation health guard is:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [2](#0-1) 

The developer comment immediately above this check even acknowledges the gap:

> *"Burning NLP can decrease health if the burn fee exceeds the health improvement from the withdrawal."* [3](#0-2) 

Every other collateral-affecting operation enforces `INITIAL` health:

- `mintNlp` — `require(getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0, ...)` [4](#0-3) 
- `withdrawCollateral` — `require(getHealth(sender, healthType) >= 0, ...)` where `healthType = INITIAL` for all non-`X_ACCOUNT` senders [5](#0-4) 
- `transferQuote` — `require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH)` [6](#0-5) 

`burnNlp` is the only user-callable collateral operation that omits the `INITIAL` health check.

---

### Impact Explanation

A subaccount can exit `burnNlp` with `INITIAL health < 0` and `MAINTENANCE health >= 0`. This state is accepted by the contract but violates the protocol's core invariant that every non-liquidatable subaccount must satisfy `INITIAL health >= 0`. The concrete consequences for the affected subaccount are:

1. **Cannot withdraw collateral** — `withdrawCollateral` requires `INITIAL health >= 0`. [7](#0-6) 
2. **Cannot open new positions or transfer quote** — both paths gate on `INITIAL` health. [6](#0-5) 
3. **Cannot be liquidated** — `MAINTENANCE health >= 0` means `isUnderMaintenance` returns false. [8](#0-7) 

The subaccount is placed in a limbo state: not liquidatable, yet unable to exit or act. This breaks the protocol's health-state machine and can trap user funds.

---

### Likelihood Explanation

Any user holding unlocked NLP can trigger this through the standard `BurnNlp` transaction path via the `Endpoint`. The condition requires the caller's `INITIAL` health to be close to zero before the burn — a realistic scenario for leveraged users who hold NLP as part of their collateral mix. The burn fee is at minimum `ONE` (1 unit in 18-decimal fixed-point), so even a tiny fee can tip a marginal account over the edge. No privileged access, governance action, or external dependency is required.

---

### Recommendation

Replace the `MAINTENANCE` health check in `burnNlp` with an `INITIAL` health check, consistent with every other collateral-affecting function:

```diff
- require(
-     getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
-     ERR_SUBACCT_HEALTH
- );
+ require(
+     getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
+     ERR_SUBACCT_HEALTH
+ );
``` [2](#0-1) 

---

### Proof of Concept

1. User holds NLP worth 1000 USDC at oracle price, and has open perp positions such that their `INITIAL health` is `+0.5 USDC` and `MAINTENANCE health` is `+50 USDC`.
2. User submits a `BurnNlp` transaction for their full NLP balance.
3. `burnNlp` computes `quoteAmount = 1000`, then `burnFee = max(ONE, 1000/1000) = 1`. Net `quoteAmount = 999`.
4. The user's quote balance increases by 999, not 1000. The 1-unit shortfall is enough to push `INITIAL health` from `+0.5` to `-0.5`.
5. The `MAINTENANCE` health check at line 527 passes (still `+49.5`), so the transaction succeeds.
6. The user now has `INITIAL health = -0.5` and `MAINTENANCE health = +49.5`.
7. Any call to `withdrawCollateral` reverts with `ERR_SUBACCT_HEALTH`. The user cannot exit their position and cannot be liquidated. Funds are effectively frozen until the user deposits additional collateral or closes positions through the sequencer. [9](#0-8)

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

**File:** core/contracts/Clearinghouse.sol (L479-482)
```text
        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/Clearinghouse.sol (L485-529)
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
