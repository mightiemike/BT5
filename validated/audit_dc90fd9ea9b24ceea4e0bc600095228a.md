### Title
No Minimum Received Quote Check in `burnNlp` — Sequencer-Supplied `oraclePriceX18` Not Covered by User Signature (`Clearinghouse.sol`)

---

### Summary

When a user burns NLP tokens via `burnNlp`, the quote amount they receive is computed entirely from an `oraclePriceX18` value that is **supplied by the sequencer** and **not covered by the user's EIP-712 signature**. The `BurnNlp` struct the user signs contains only `sender`, `nlpAmount`, and `nonce` — no minimum acceptable quote amount. There is no on-chain slippage guard. A user can receive arbitrarily less quote than expected with no recourse.

---

### Finding Description

The `BurnNlp` struct that the user signs is:

```solidity
struct BurnNlp {
    bytes32 sender;
    uint128 nlpAmount;
    uint64 nonce;
}
``` [1](#0-0) 

The outer `SignedBurnNlp` struct appends `oraclePriceX18` and `nlpPoolRebalanceX18` outside the signed body:

```solidity
struct SignedBurnNlp {
    BurnNlp tx;
    bytes signature;
    int128 oraclePriceX18;
    int128[] nlpPoolRebalanceX18;
}
``` [2](#0-1) 

In `EndpointTx.processTransactionImpl`, `validateSignedTx` is called with the inner `signedTx.tx` fields (`sender`, `nonce`) and the full `transaction` bytes. The EIP-712 digest is computed over the `BurnNlp` type, which does **not** include `oraclePriceX18`. The sequencer then passes `signedTx.oraclePriceX18` directly to `clearinghouse.burnNlp`:

```solidity
validateSignedTx(signedTx.tx.sender, signedTx.tx.nonce, transaction, signedTx.signature, true);
chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
clearinghouse.burnNlp(signedTx.tx, signedTx.oraclePriceX18, nlpPools, signedTx.nlpPoolRebalanceX18);
``` [3](#0-2) 

Inside `Clearinghouse.burnNlp`, the quote amount returned to the user is:

```solidity
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
``` [4](#0-3) 

There is no check of the form `require(quoteAmount >= minQuoteAmount)`. The user has no mechanism to express a floor on what they will accept.

The same pattern applies symmetrically to `mintNlp`: the user signs `MintNlp { sender, quoteAmount, nonce }` and the sequencer supplies `oraclePriceX18`, which determines `nlpAmount = quoteAmount.div(oraclePriceX18)` — again with no minimum NLP amount check. [5](#0-4) 

Notably, `ERR_SLIPPAGE_TOO_HIGH` is defined in `Errors.sol` but is **never referenced** in any production contract, confirming slippage protection was anticipated but not implemented for NLP operations. [6](#0-5) 

---

### Impact Explanation

A user who submits a `BurnNlp` transaction commits to burning a fixed `nlpAmount`. The quote they receive is `nlpAmount × oraclePriceX18 − burnFee`. If the sequencer uses a stale or depressed oracle price — whether due to latency, market volatility, or deliberate ordering — the user receives materially less quote than the fair market value of the NLP they burned. The NLP tokens are already debited (`spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount)`) before the quote is credited, so the loss is irreversible once the transaction is processed. [7](#0-6) 

---

### Likelihood Explanation

Medium. The sequencer is the sole provider of `oraclePriceX18` for NLP operations and this value is not validated against any on-chain price feed or bounded relative to a recent reference price. During periods of high NLP price volatility, even an honest sequencer processing a backlog of transactions could settle burns at a price significantly below the price at the time the user signed. A user has no on-chain protection and cannot cancel a submitted transaction once it enters the sequencer queue.

---

### Recommendation

Add a `minQuoteAmount` field to the `BurnNlp` struct (so it is covered by the user's EIP-712 signature) and enforce it in `Clearinghouse.burnNlp`:

```solidity
struct BurnNlp {
    bytes32 sender;
    uint128 nlpAmount;
    uint64 nonce;
    int128 minQuoteAmount; // user-specified floor
}
```

Then in `burnNlp`, after computing `quoteAmount`:

```solidity
require(quoteAmount >= txn.minQuoteAmount, ERR_SLIPPAGE_TOO_HIGH);
```

Apply the same fix symmetrically to `MintNlp` with a `minNlpAmount` field.

---

### Proof of Concept

1. User signs `BurnNlp { sender: alice, nlpAmount: 1000e18, nonce: 5 }` when NLP oracle price is `$10`, expecting ~`$9990` quote (after 0.1% fee).
2. Sequencer delays processing. NLP oracle price drops to `$7`.
3. Sequencer submits the transaction with `oraclePriceX18 = 7e18`.
4. `quoteAmount = 1000e18 * 7e18 / 1e18 = 7000e18`; after fee: `~6993e18`.
5. Alice receives `~$6993` instead of `~$9990` — a `~$3000` shortfall — with no revert and no recourse.
6. The `BurnNlp` struct Alice signed contained no `minQuoteAmount`, so the sequencer's price choice is entirely unconstrained on-chain. [8](#0-7)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L125-129)
```text
    struct BurnNlp {
        bytes32 sender;
        uint128 nlpAmount;
        uint64 nonce;
    }
```

**File:** core/contracts/interfaces/IEndpoint.sol (L131-136)
```text
    struct SignedBurnNlp {
        BurnNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
    }
```

**File:** core/contracts/EndpointTx.sol (L559-573)
```text
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

**File:** core/contracts/Clearinghouse.sol (L464-466)
```text
        require(txn.quoteAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);
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

**File:** core/contracts/common/Errors.sol (L90-90)
```text
string constant ERR_SLIPPAGE_TOO_HIGH = "STH";
```
