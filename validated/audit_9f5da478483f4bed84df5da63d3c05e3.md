### Title
`burnNlp` Checks Only Maintenance Health, Missing Initial Health Guard — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

`burnNlp` in `Clearinghouse.sol` enforces only a `MAINTENANCE` health check after burning NLP tokens and crediting quote tokens to the sender. The analogous collateral-removal function `withdrawCollateral` enforces the stricter `INITIAL` health check. A user whose account is already under-initial (INITIAL health < 0) but above-maintenance (MAINTENANCE health ≥ 0) can burn NLP to extract quote tokens — a path that `withdrawCollateral` would block outright.

---

### Finding Description

`withdrawCollateral` selects health type based on sender identity:

```solidity
IProductEngine.HealthType healthType = sender == X_ACCOUNT
    ? IProductEngine.HealthType.PNL
    : IProductEngine.HealthType.INITIAL;
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
``` [1](#0-0) 

For any normal subaccount this enforces `INITIAL` health ≥ 0 — the stricter threshold that prevents opening new positions or withdrawing collateral when the account is over-leveraged.

`burnNlp`, however, only checks `MAINTENANCE` health:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [2](#0-1) 

The in-code comment explicitly acknowledges the intent to block unhealthy-subaccount creation via burns, yet the guard used (`MAINTENANCE`) is weaker than the one used for direct withdrawals (`INITIAL`). The gap between the two thresholds is the exploitable window. [3](#0-2) 

---

### Impact Explanation

A user whose account satisfies `MAINTENANCE health ≥ 0` but `INITIAL health < 0` (i.e., under-initial but not yet liquidatable) can call `burnNlp` to convert NLP tokens into quote tokens. This is economically equivalent to a collateral withdrawal that `withdrawCollateral` would reject with `ERR_SUBACCT_HEALTH`. The user extracts quote collateral that should be backing existing liabilities, worsening the protocol's risk exposure for that subaccount without triggering the intended guard. The corrupted state is the subaccount's quote balance and its INITIAL health, both of which move in the wrong direction relative to the protocol's invariant that collateral cannot be removed when INITIAL health is already negative.

---

### Likelihood Explanation

The condition (INITIAL health < 0, MAINTENANCE health ≥ 0) is a normal intermediate state for any leveraged subaccount that has drifted below initial margin but has not yet crossed the liquidation threshold. Any user holding NLP tokens in such a subaccount can trigger this path. The `burnNlp` function is reachable via the sequencer processing a user-submitted `BurnNlp` transaction type through `onlyEndpoint`; no privileged access beyond normal protocol participation is required. [4](#0-3) 

---

### Recommendation

Replace the `MAINTENANCE` health check in `burnNlp` with an `INITIAL` health check, consistent with `withdrawCollateral`:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
    ERR_SUBACCT_HEALTH
);
```

This closes the gap between the two collateral-removal paths and enforces a uniform invariant: no subaccount may reduce its collateral backing when INITIAL health is already negative.

---

### Proof of Concept

1. Alice opens a leveraged perp position. Market moves against her; her INITIAL health drops to −5 (under-initial) but MAINTENANCE health remains +3 (above liquidation threshold).
2. `withdrawCollateral` reverts: `getHealth(alice, INITIAL) < 0` → `ERR_SUBACCT_HEALTH`.
3. Alice holds NLP tokens. She submits a `BurnNlp` transaction.
4. `burnNlp` executes: NLP is burned, quote tokens are credited to Alice.
5. Post-burn check: `getHealth(alice, MAINTENANCE) >= 0` — passes, because MAINTENANCE health is still positive.
6. Alice has successfully extracted quote collateral despite being under-initial, a state `withdrawCollateral` would have blocked.
7. Alice's INITIAL health is now even more negative; her liabilities are less collateralized, increasing protocol risk. [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/Clearinghouse.sol (L391-420)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
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
