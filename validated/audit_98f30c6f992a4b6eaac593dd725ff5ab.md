### Title
`BurnNlp` Lacks Minimum Quote Return Guard, Exposing Users to Adverse Price Movement — (File: `core/contracts/Clearinghouse.sol`)

### Summary

The `burnNlp` function in `Clearinghouse.sol` computes the quote amount returned to the user using an `oraclePriceX18` that is **not part of the user's EIP-712 signed message**. The user signs only `sender`, `nlpAmount`, and `nonce`. No `minQuoteAmount` field exists in the signed struct. If the NLP price drops between when the user submits the `BurnNlp` request and when the sequencer executes it, the user receives less quote than expected with no on-chain protection.

### Finding Description

The `BurnNlp` EIP-712 type string is:

```
"BurnNlp(bytes32 sender,uint128 nlpAmount,uint64 nonce)"
``` [1](#0-0) 

The `oraclePriceX18` lives in the outer `SignedBurnNlp` wrapper and is never committed to by the user's signature:

```solidity
struct SignedBurnNlp {
    BurnNlp tx;          // ← user signs only this
    bytes signature;
    int128 oraclePriceX18;       // ← NOT signed
    int128[] nlpPoolRebalanceX18; // ← NOT signed
}
``` [2](#0-1) 

Inside `burnNlp`, the quote returned to the user is computed entirely from this unsigned price:

```solidity
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
``` [3](#0-2) 

There is no check of the form `require(quoteAmount >= minQuoteAmount)` anywhere in `burnNlp`. The function simply credits whatever `quoteAmount` results from the sequencer-supplied price. [4](#0-3) 

The execution path is: user submits `BurnNlp` off-chain → sequencer calls `EndpointTx` → `clearinghouse.burnNlp(signedTx.tx, signedTx.oraclePriceX18, ...)`. [5](#0-4) 

### Impact Explanation

A user burning `nlpAmount` NLP tokens can receive materially less quote than they expected. In the extreme case where `oraclePriceX18` is very low, `quoteAmount` is clamped to `0` by `MathHelper.max(0, quoteAmount - burnFee)`, meaning the user burns their NLP tokens and receives **zero quote**. The corrupted state delta is the user's `QUOTE_PRODUCT_ID` balance, which is credited with less (potentially zero) than the user anticipated when signing. [3](#0-2) 

### Likelihood Explanation

NLP token value is derived from the pool's NAV, which fluctuates with market conditions. There is an inherent latency between when a user signs and submits a `BurnNlp` request and when the sequencer includes and executes it. During volatile market periods this gap can be significant. Because the user's signature commits to no price bound, every `BurnNlp` execution is subject to this risk. No special privilege or compromise is required — this is triggered by ordinary market price movement during normal sequencer operation.

### Recommendation

Add a `minQuoteAmount` field to the `BurnNlp` struct (and include it in the EIP-712 type string and digest computation in `Verifier.sol`). In `burnNlp`, add:

```solidity
require(quoteAmount >= int128(txn.minQuoteAmount), ERR_SLIPPAGE_TOO_HIGH);
```

Analogously, add a `minNlpAmount` guard to `mintNlp` for the symmetric case where a rising NLP price causes the user to receive fewer NLP tokens than expected. [6](#0-5) 

### Proof of Concept

1. Bob signs `BurnNlp { sender: bob, nlpAmount: 1000e18, nonce: 5 }` when the NLP oracle price is `1.00 USDC` per NLP, expecting to receive approximately `999 USDC` (after the 0.1% burn fee).
2. Before the sequencer executes the transaction, the NLP pool suffers a loss and the price drops to `0.50 USDC`.
3. The sequencer submits the transaction with `oraclePriceX18 = 0.50e18`.
4. `burnNlp` computes `quoteAmount = 1000e18 * 0.50e18 / 1e18 = 500e18`, then deducts `burnFee = max(1, 500e18/1000) = 0.5e18`, crediting Bob with `499.5 USDC`.
5. Bob's signature was valid — he committed to burning 1000 NLP but had no on-chain protection against receiving only ~50% of the expected return.
6. Bob receives `499.5 USDC` instead of the expected `~999 USDC`, a loss of approximately `499.5 USDC` with no recourse. [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/Verifier.sol (L28-29)
```text
    string internal constant BURN_NLP_SIGNATURE =
        "BurnNlp(bytes32 sender,uint128 nlpAmount,uint64 nonce)";
```

**File:** core/contracts/Verifier.sol (L386-398)
```text
        } else if (txType == IEndpoint.TransactionType.BurnNlp) {
            IEndpoint.SignedBurnNlp memory signedTx = abi.decode(
                transactionBody,
                (IEndpoint.SignedBurnNlp)
            );
            digest = keccak256(
                abi.encode(
                    keccak256(bytes(BURN_NLP_SIGNATURE)),
                    signedTx.tx.sender,
                    signedTx.tx.nlpAmount,
                    signedTx.tx.nonce
                )
            );
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

**File:** core/contracts/Clearinghouse.sol (L496-504)
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
```

**File:** core/contracts/Clearinghouse.sol (L514-516)
```text
        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
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
