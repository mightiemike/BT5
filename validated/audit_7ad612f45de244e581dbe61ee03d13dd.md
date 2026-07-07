### Title
NLP Tokens Burned Without Returning Quote When Value ≤ Minimum Burn Fee — (`core/contracts/Clearinghouse.sol`)

---

### Summary

In `Clearinghouse.burnNlp`, when a user burns NLP tokens whose computed quote value is less than or equal to the minimum burn fee (`ONE`), the function silently sets `quoteAmount = 0`, burns the NLP balance, and returns nothing to the user. The transaction does not revert. This is a direct analog of M06: tokens are consumed but zero underlying asset is transferred in exchange.

---

### Finding Description

The `burnNlp` function computes the quote to return as follows:

```solidity
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);          // line 502
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);    // line 503
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);      // line 504
```

`MathSD21x18.mul` is a fixed-point multiply: `(nlpAmount * oraclePriceX18) / 1e18`. [1](#0-0) 

The minimum burn fee is `ONE` (= `1e18` in X18 format, i.e., 1 unit of normalized quote). [2](#0-1) 

When `quoteAmount <= ONE`, the subtraction `quoteAmount - burnFee` is ≤ 0, so `MathHelper.max(0, ...)` clamps it to `0`. [3](#0-2) 

The NLP balance is then unconditionally reduced:

```solidity
spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);   // line 511
spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);     // line 512
```

But the quote credit is gated:

```solidity
if (quoteAmount > 0) {                                               // line 514
    spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
    _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
}
``` [4](#0-3) 

When `quoteAmount == 0`, the NLP is burned and the N_ACCOUNT receives it, but the user's quote balance is never credited. The function does not revert.

---

### Impact Explanation

A user who submits a `BurnNlp` transaction with `nlpAmount` small enough that `nlpAmount.mul(oraclePriceX18) <= ONE` will:

1. Lose their NLP tokens permanently (balance decremented, N_ACCOUNT credited).
2. Receive **zero** quote in return.
3. Have no on-chain protection — the transaction succeeds silently.

The corrupted state delta: `spotEngine` balance for `NLP_PRODUCT_ID` on `txn.sender` is reduced by `nlpAmount`, while `QUOTE_PRODUCT_ID` balance is unchanged. The user suffers a direct asset loss with no recourse.

**Concrete numeric example** (assuming `ONE = 1e18`, `oraclePriceX18 = 1e18` i.e. 1 USDC/NLP):
- User burns `nlpAmount = 5e17` (0.5 NLP in X18).
- `quoteAmount = (5e17 * 1e18) / 1e18 = 5e17`.
- `burnFee = max(1e18, 5e17/1000) = 1e18`.
- `quoteAmount = max(0, 5e17 - 1e18) = 0`.
- 0.5 NLP burned, 0 USDC returned. No revert. [5](#0-4) 

---

### Likelihood Explanation

The trigger is reachable by any user who submits a `BurnNlp` transaction with a small `nlpAmount`. The user signs only `{ sender, nlpAmount, nonce }` — the sequencer appends `oraclePriceX18`. [6](#0-5) 

The sequencer processes the transaction via `EndpointTx`, which calls `clearinghouse.burnNlp`. [7](#0-6) 

No special privileges are required. Any user holding NLP tokens can submit a small burn. The condition `nlpAmount.mul(oraclePriceX18) <= ONE` is easily satisfied for small amounts or when NLP price is low. The `_validateNlpRebalance` check passes trivially when `quoteAmount = 0` because the sequencer supplies `nlpPoolRebalanceX18` summing to `0`. [8](#0-7) 

---

### Recommendation

Add a check after computing `quoteAmount` to revert if the user would receive nothing:

```solidity
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
require(quoteAmount > 0, "ERR_INSUFFICIENT_NLP_AMOUNT");
```

This mirrors the fix applied in Aave MR#79 and ensures the transaction reverts rather than silently consuming NLP tokens for zero return.

---

### Proof of Concept

1. User holds NLP tokens and submits a `BurnNlp` with `nlpAmount = 5e17` (0.5 NLP in X18 units).
2. Sequencer processes the transaction, providing `oraclePriceX18 = 1e18` and `nlpPoolRebalanceX18 = [0]`.
3. In `burnNlp`:
   - `quoteAmount = (5e17 * 1e18) / 1e18 = 5e17`
   - `burnFee = max(1e18, 5e14) = 1e18`
   - `quoteAmount = max(0, 5e17 - 1e18) = 0`
4. `spotEngine.updateBalance(NLP_PRODUCT_ID, sender, -5e17)` executes — NLP burned.
5. `if (quoteAmount > 0)` is false — no quote credited.
6. Transaction succeeds. User loses 0.5 NLP, receives 0 USDC. [9](#0-8)

### Citations

**File:** core/contracts/libraries/MathSD21x18.sol (L54-59)
```text
    function mul(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            int256 result = (int256(x) * y) / ONE_X18;
            require(result >= MIN_X18 && result <= MAX_X18, ERR_OVERFLOW);
            return int128(result);
        }
```

**File:** core/contracts/Clearinghouse.sol (L423-437)
```text
    function _validateNlpRebalance(
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18,
        int128 deltaQuoteAmount
    ) internal pure {
        require(
            nlpPools.length == nlpPoolRebalanceX18.length,
            ERR_INVALID_NLP_REBALANCE
        );
        int128 rebalanceAmount = 0;
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            rebalanceAmount += nlpPoolRebalanceX18[i];
        }
        require(deltaQuoteAmount == rebalanceAmount, ERR_INVALID_NLP_REBALANCE);
    }
```

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

**File:** core/contracts/libraries/MathHelper.sol (L11-13)
```text
    function max(int128 a, int128 b) internal pure returns (int128) {
        return a > b ? a : b;
    }
```

**File:** core/contracts/interfaces/IEndpoint.sol (L125-136)
```text
    struct BurnNlp {
        bytes32 sender;
        uint128 nlpAmount;
        uint64 nonce;
    }

    struct SignedBurnNlp {
        BurnNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
    }
```

**File:** core/contracts/EndpointTx.sol (L554-570)
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
```
