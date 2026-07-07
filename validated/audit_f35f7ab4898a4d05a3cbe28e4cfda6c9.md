### Title
Minimum Burn Fee Can Consume 100% of Small NLP Positions — (`core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.burnNlp` applies a hardcoded minimum burn fee of `$1` (`ONE = 10^18`). When a user's NLP position is worth less than `$1` at the time of burning, the entire position value is consumed by the fee and the user receives zero quote tokens back, while their NLP balance is fully decremented.

---

### Finding Description

In `Clearinghouse.burnNlp`, the quote amount returned to the user is computed as:

```solidity
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
``` [1](#0-0) 

Where `ONE = 10^18` represents `$1` in the protocol's fixed-point math. [2](#0-1) 

The `burnFee` is `max($1, 0.1% of quoteAmount)`. For any NLP position worth less than `$1,000`, the minimum `$1` fee dominates. For any position worth less than `$1`, `burnFee > quoteAmount`, so:

```solidity
quoteAmount = MathHelper.max(0, quoteAmount - burnFee); // = 0
```

The NLP balance is still fully decremented regardless:

```solidity
spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);

if (quoteAmount > 0) {
    spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
    ...
}
``` [3](#0-2) 

The `if (quoteAmount > 0)` guard silently skips the quote credit, so the user's NLP tokens are burned with zero compensation.

---

### Impact Explanation

A user with an NLP position worth less than `$1` (in quote terms at the oracle price used at burn time) loses 100% of their position value. The NLP tokens are destroyed and no quote is credited. This is a direct, irreversible asset loss for the user.

---

### Likelihood Explanation

This is reachable without any privileged actor:

1. A user mints NLP via `mintNlp` with a valid deposit (minimum `$5` for first deposit).
2. The NLP oracle price subsequently drops, or the user accumulates a small NLP balance via `nlpProfitShare`.
3. The user submits a `BurnNlp` signed transaction through the `Endpoint`.
4. `EndpointTx` routes it to `Clearinghouse.burnNlp`.
5. If `nlpAmount * oraclePriceX18 < ONE`, the user receives 0 quote. [4](#0-3) 

The `nlpProfitShare` function can credit small NLP amounts to arbitrary subaccounts, making it easy for users to accumulate sub-`$1` NLP positions that are unrecoverable.

---

### Recommendation

Add a minimum received quote check after fee deduction, reverting if the user would receive nothing:

```solidity
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
require(quoteAmount > 0, "BurnNlp: fee exceeds position value");
```

Alternatively, enforce a minimum `nlpAmount` for burns such that `nlpAmount * oraclePriceX18 > burnFee` is always guaranteed before proceeding.

---

### Proof of Concept

1. User holds `nlpAmount = 0.5 * 10^18` NLP tokens (0.5 NLP units).
2. Oracle price is `oraclePriceX18 = 1.5 * 10^18` ($1.50 per NLP).
3. `quoteAmount = 0.5e18 * 1.5e18 / 1e18 = 0.75e18` ($0.75).
4. `burnFee = max(1e18, 0.75e18 / 1000) = max(1e18, 7.5e14) = 1e18` ($1.00).
5. `quoteAmount = max(0, 0.75e18 - 1e18) = 0`.
6. `spotEngine.updateBalance(NLP_PRODUCT_ID, sender, -0.5e18)` — NLP burned.
7. `if (quoteAmount > 0)` is false — no quote credited.
8. User loses $0.75 worth of NLP with zero compensation. [5](#0-4)

### Citations

**File:** core/contracts/Clearinghouse.sol (L496-517)
```text
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
```

**File:** core/contracts/common/Constants.sol (L17-17)
```text
int128 constant ONE = 10**18;
```
