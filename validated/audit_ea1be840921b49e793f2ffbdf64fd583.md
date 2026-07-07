### Title
Missing `oraclePriceX18` Validation in `mintNlp`/`burnNlp` Allows Zero-Price NLP Accounting Corruption — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.mintNlp` and `Clearinghouse.burnNlp` accept a sequencer-supplied `oraclePriceX18` parameter that is never validated to be positive, unlike `Clearinghouse.updatePrice` which explicitly enforces `require(txn.priceX18 > 0, ERR_INVALID_PRICE)`. If the sequencer submits `oraclePriceX18 = 0` or a negative value, `burnNlp` silently burns the user's NLP balance and returns zero quote — a direct, unrecoverable loss of funds.

---

### Finding Description

`Clearinghouse.updatePrice` enforces a strict positivity check on any incoming price: [1](#0-0) 

However, `mintNlp` and `burnNlp` accept `oraclePriceX18` as a raw `int128` parameter and use it directly in arithmetic without any equivalent guard: [2](#0-1) [3](#0-2) 

Critically, `oraclePriceX18` is **not covered by the user's EIP-712 signature**. The `MINT_NLP_SIGNATURE` and `BURN_NLP_SIGNATURE` in `Verifier.sol` only commit to `sender`, `quoteAmount`/`nlpAmount`, and `nonce` — the price field is absent from both digests: [4](#0-3) [5](#0-4) 

The sequencer freely appends `oraclePriceX18` when constructing the `SignedMintNlp` / `SignedBurnNlp` payload and passes it directly to the clearinghouse: [6](#0-5) [7](#0-6) 

**Concrete execution trace for `burnNlp` with `oraclePriceX18 = 0`:**

1. Line 502: `quoteAmount = nlpAmount.mul(0) = 0`
2. Line 503: `burnFee = MathHelper.max(ONE, 0 / 1000) = ONE` (1e18)
3. Line 504: `quoteAmount = MathHelper.max(0, 0 − ONE) = 0`
4. Line 511: user's NLP balance is decremented by `nlpAmount`
5. Line 514: `if (quoteAmount > 0)` evaluates false — no quote is returned

The user's NLP tokens are permanently burned; they receive nothing.

For `mintNlp` with `oraclePriceX18 = 0`, line 466 performs `quoteAmount.div(0)`, which reverts — so `mintNlp` is self-protecting via revert, but `burnNlp` silently corrupts state.

---

### Impact Explanation

**High.** A user who submits a valid `BurnNlp` signed transaction loses their entire NLP position and receives zero quote in return. The NLP tokens are transferred to `N_ACCOUNT` with no corresponding quote credit. This is an unrecoverable, direct loss of user funds with no on-chain mechanism to reverse it. [8](#0-7) 

---

### Likelihood Explanation

**Low.** Exploiting this requires the sequencer to submit `oraclePriceX18 = 0` or a negative value when processing a user's `BurnNlp` transaction. This mirrors the original report's "malfunctioning price feed" scenario — it requires either a sequencer bug or a sequencer acting outside its expected behavior. The protocol already demonstrates awareness of this risk by guarding `updatePrice` with `require(txn.priceX18 > 0)`, making the absence of the same guard in `burnNlp` an oversight rather than intentional design.

---

### Recommendation

Add an explicit positivity check at the top of both `mintNlp` and `burnNlp`, consistent with the guard already present in `updatePrice`:

```solidity
require(oraclePriceX18 > 0, ERR_INVALID_PRICE);
```

This should be inserted before any arithmetic use of `oraclePriceX18` in both functions. [9](#0-8) [10](#0-9) 

---

### Proof of Concept

1. User signs a `BurnNlp` transaction committing to `sender`, `nlpAmount = X`, `nonce`.
2. Sequencer constructs `SignedBurnNlp` with `oraclePriceX18 = 0` (not covered by user signature).
3. Sequencer calls `submitTransactions` → `EndpointTx` dispatches to `clearinghouse.burnNlp(txn, 0, ...)`.
4. Inside `burnNlp`: `quoteAmount = X.mul(0) = 0`; `burnFee = ONE`; `quoteAmount = max(0, -ONE) = 0`.
5. `spotEngine.updateBalance(NLP_PRODUCT_ID, sender, -X)` — user loses X NLP.
6. `if (quoteAmount > 0)` is false — user receives 0 USDC.
7. Health checks pass (burning NLP improves health).
8. User has lost X NLP tokens with zero compensation. [11](#0-10)

### Citations

**File:** core/contracts/Clearinghouse.sol (L367-367)
```text
        require(txn.priceX18 > 0, ERR_INVALID_PRICE);
```

**File:** core/contracts/Clearinghouse.sol (L453-466)
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
```

**File:** core/contracts/Clearinghouse.sol (L485-516)
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
```

**File:** core/contracts/Verifier.sol (L26-29)
```text
    string internal constant MINT_NLP_SIGNATURE =
        "MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce)";
    string internal constant BURN_NLP_SIGNATURE =
        "BurnNlp(bytes32 sender,uint128 nlpAmount,uint64 nonce)";
```

**File:** core/contracts/Verifier.sol (L373-398)
```text
        } else if (txType == IEndpoint.TransactionType.MintNlp) {
            IEndpoint.SignedMintNlp memory signedTx = abi.decode(
                transactionBody,
                (IEndpoint.SignedMintNlp)
            );
            digest = keccak256(
                abi.encode(
                    keccak256(bytes(MINT_NLP_SIGNATURE)),
                    signedTx.tx.sender,
                    signedTx.tx.quoteAmount,
                    signedTx.tx.nonce
                )
            );
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

**File:** core/contracts/EndpointTx.sol (L547-553)
```text
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.mintNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
```

**File:** core/contracts/EndpointTx.sol (L567-573)
```text
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.burnNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
```
