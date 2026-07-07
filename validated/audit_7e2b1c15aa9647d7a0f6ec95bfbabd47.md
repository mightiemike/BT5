### Title
`burnNlp()` Does Not Check NLP Pool Health After Quote Drain, Enabling Targeted Pool Insolvency — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.burnNlp()` allows a user to specify exactly how the quote payout is sourced from individual NLP pool subaccounts via `nlpPoolRebalanceX18`. After applying the rebalance, only the **sender's** health is verified. The health of the NLP pool subaccounts that lose quote is never checked. A user can concentrate the entire quote drain onto a single NLP pool, pushing it below maintenance health and making it liquidatable.

---

### Finding Description

When a user burns NLP tokens, `burnNlp` computes `quoteAmount` and calls `_applyNlpRebalance`, which subtracts quote from each NLP pool subaccount according to the user-supplied `nlpPoolRebalanceX18` array. [1](#0-0) 

The only post-rebalance health check is on `txn.sender`: [2](#0-1) 

No health check is performed on any NLP pool subaccount. The `_applyNlpRebalance` helper blindly applies the deltas: [3](#0-2) 

The on-chain constraints on `nlpPoolRebalanceX18` are only:
1. Each entry `<= 0` (pools can only lose quote)
2. The sum equals `-quoteAmount` [4](#0-3) 

There is no constraint preventing the user from concentrating the full drain onto a single pool (e.g., `[-quoteAmount, 0, 0, ...]`). The user signs the entire `SignedBurnNlp` struct including `nlpPoolRebalanceX18`, so this distribution is fully user-controlled and validated on-chain: [5](#0-4) 

---

### Impact Explanation

An NLP pool subaccount whose quote balance is driven negative will borrow quote. If the resulting health falls below the maintenance threshold, the pool becomes liquidatable by any caller via `liquidateSubaccountImpl`. NLP pool subaccounts are not excluded from liquidation (only `X_ACCOUNT` and `N_ACCOUNT` are): [6](#0-5) 

A liquidator can then seize the pool's NLP tokens at a discount, permanently removing protocol-owned liquidity. This is a direct, irreversible asset loss from the protocol's NLP liquidity system.

**Impact: High**

---

### Likelihood Explanation

Any user who holds unlocked NLP tokens can trigger this. The `BurnNlp` transaction type is processed through the standard sequencer path (`processTransactionImpl`), not the owner-only slow-mode path. The user signs the `nlpPoolRebalanceX18` array, so the malicious distribution is embedded in a fully valid, on-chain-verifiable transaction. No privileged access is required. [7](#0-6) 

**Likelihood: High**

---

### Recommendation

After `_applyNlpRebalance` in `burnNlp`, iterate over all NLP pools and assert that each pool subaccount's health remains at or above the initial (or at minimum maintenance) threshold, mirroring the pattern used in `forceRebalanceNlpPool` (which itself has an off-by-one but demonstrates the intended pattern): [8](#0-7) 

Add an analogous loop in `burnNlp` after `_applyNlpRebalance`:

```solidity
for (uint128 i = 0; i < nlpPools.length; i++) {
    require(
        getHealth(nlpPools[i].subaccount, IProductEngine.HealthType.INITIAL) >= 0,
        ERR_SUBACCT_HEALTH
    );
}
```

---

### Proof of Concept

1. User holds `X` unlocked NLP tokens. There are 3 NLP pools: `pool0`, `pool1`, `pool2`.
2. User constructs a `BurnNlp` transaction with:
   - `nlpAmount = X`
   - `nlpPoolRebalanceX18 = [-quoteAmount, 0, 0]` (entire drain on `pool0`)
3. User signs the transaction (signature covers `nlpPoolRebalanceX18`).
4. Sequencer includes the transaction in a batch; `processTransactionImpl` calls `clearinghouse.burnNlp(...)`.
5. `_applyNlpRebalance` subtracts `quoteAmount` from `pool0`'s quote balance. `pool0`'s quote balance goes negative.
6. Only `txn.sender`'s health is checked — passes.
7. `pool0`'s health is now below maintenance. Any liquidator calls `liquidateSubaccount` targeting `pool0`, seizing its NLP tokens at a discount.
8. Protocol permanently loses NLP liquidity from `pool0`. [9](#0-8)

### Citations

**File:** core/contracts/Clearinghouse.sol (L439-451)
```text
    function _applyNlpRebalance(
        ISpotEngine spotEngine,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) internal {
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                nlpPools[i].subaccount,
                nlpPoolRebalanceX18[i]
            );
        }
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

**File:** core/contracts/Clearinghouse.sol (L540-548)
```text
        for (uint128 i = 1; i < nlpPools.length; i++) {
            require(
                getHealth(
                    nlpPools[i].subaccount,
                    IProductEngine.HealthType.INITIAL
                ) >= 0,
                ERR_SUBACCT_HEALTH
            );
        }
```

**File:** core/contracts/EndpointTx.sol (L554-574)
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
        } else if (txType == IEndpoint.TransactionType.ManualAssert) {
```

**File:** core/contracts/ClearinghouseLiq.sol (L601-607)
```text
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
        require(
            txn.liquidatee != X_ACCOUNT && txn.liquidatee != N_ACCOUNT,
            ERR_NOT_LIQUIDATABLE
        );
```
