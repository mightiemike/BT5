### Title
Missing User-Defined Slippage Bounds on `MintNlp`/`BurnNlp` — `oraclePriceX18` Excluded from User Signature Digest — (`core/contracts/interfaces/IEndpoint.sol`, `core/contracts/Clearinghouse.sol`, `core/contracts/Verifier.sol`)

---

### Summary

The `MintNlp` and `BurnNlp` transaction structs carry no user-defined minimum output fields. The `oraclePriceX18` that determines the NLP/quote exchange rate is supplied by the sequencer in the outer `SignedMintNlp`/`SignedBurnNlp` wrapper and is **not included in the EIP-712 digest the user signs**. The user's signature commits only to `{sender, quoteAmount, nonce}` (mint) or `{sender, nlpAmount, nonce}` (burn). Because the price is sequencer-controlled and unconstrained by user intent, the sequencer can execute either operation at an arbitrarily unfavorable price with no on-chain revert path.

---

### Finding Description

**`MintNlp` struct** contains only `sender`, `quoteAmount`, and `nonce` — no `minNlpAmount`: [1](#0-0) 

**`BurnNlp` struct** contains only `sender`, `nlpAmount`, and `nonce` — no `minQuoteAmount`: [2](#0-1) 

The `oraclePriceX18` field lives in the outer sequencer-populated wrappers `SignedMintNlp` and `SignedBurnNlp`, outside the user-signed payload: [3](#0-2) 

In `Verifier.sol`, the EIP-712 digest for `MintNlp` hashes only `{sender, quoteAmount, nonce}` — `oraclePriceX18` is absent: [4](#0-3) 

Likewise for `BurnNlp`, the digest hashes only `{sender, nlpAmount, nonce}`: [5](#0-4) 

In `EndpointTx.sol`, the sequencer-supplied `signedTx.oraclePriceX18` is passed directly into `clearinghouse.mintNlp` / `clearinghouse.burnNlp` after signature validation that never covered it: [6](#0-5) [7](#0-6) 

In `Clearinghouse.sol`, `mintNlp` computes `nlpAmount = quoteAmount / oraclePriceX18` — inflating the price reduces NLP minted to the user: [8](#0-7) 

In `burnNlp`, `quoteAmount = nlpAmount * oraclePriceX18` — deflating the price reduces quote returned to the user: [9](#0-8) 

There is no `require` anywhere in either function that checks a user-supplied minimum output threshold. [10](#0-9) [11](#0-10) 

---

### Impact Explanation

**For `MintNlp`:** A sequencer supplying an inflated `oraclePriceX18` causes `nlpAmount = quoteAmount / oraclePriceX18` to be arbitrarily small. The user's full `quoteAmount` is debited from their spot balance while they receive a fraction of the NLP tokens they expected. The difference in NLP value is silently absorbed.

**For `BurnNlp`:** A sequencer supplying a deflated `oraclePriceX18` causes `quoteAmount = nlpAmount * oraclePriceX18` to be arbitrarily small. The user's full `nlpAmount` is burned while they receive a fraction of the quote they expected. The `burnFee` is also computed on the deflated `quoteAmount`, so the fee does not compensate for the loss.

In both cases the corrupted state delta is a direct, measurable asset loss from the user's subaccount balance with no revert path available to the user.

---

### Likelihood Explanation

The sequencer is the sole entity that constructs and submits `SignedMintNlp`/`SignedBurnNlp` transactions, including the `oraclePriceX18` field. Because that field is structurally excluded from the user's EIP-712 digest, the protocol provides **zero on-chain enforcement** of the price the user implicitly agreed to when signing. A compromised or malicious sequencer can exploit this on every `MintNlp`/`BurnNlp` transaction without any additional precondition. The protocol's centralized sequencer model makes this a realistic threat surface.

---

### Recommendation

**Short term:** Add `minNlpAmount` to `MintNlp` and `minQuoteAmount` to `BurnNlp`. Include both fields in the EIP-712 digest computed in `Verifier.sol`. Enforce them in `Clearinghouse.mintNlp` and `Clearinghouse.burnNlp` with a `require(nlpAmount >= txn.minNlpAmount)` / `require(quoteAmount >= txn.minQuoteAmount)` guard before any balance update.

**Long term:** Add tests that supply a manipulated `oraclePriceX18` and assert that the transaction reverts when the computed output falls below the user-specified minimum.

---

### Proof of Concept

1. User signs `MintNlp{sender=Alice, quoteAmount=1000e18, nonce=1}` expecting to receive NLP at the current fair price of, say, `1e18` (1:1), yielding `1000` NLP.
2. Sequencer constructs `SignedMintNlp{tx: <above>, signature: <valid>, oraclePriceX18: 1000e18, nlpPoolRebalanceX18: [...]}` — inflating the price 1000×.
3. `validateSignedTx` passes because `oraclePriceX18` is not in the digest.
4. `Clearinghouse.mintNlp` computes `nlpAmount = 1000e18 / 1000e18 = 1` — Alice receives 1 NLP unit instead of 1000.
5. Alice's `quoteAmount` of `1000e18` is fully debited. No `require` fires. The transaction succeeds.
6. The symmetric attack applies to `BurnNlp` by deflating `oraclePriceX18` to near zero, returning negligible quote for the burned NLP.

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L112-116)
```text
    struct MintNlp {
        bytes32 sender;
        uint128 quoteAmount;
        uint64 nonce;
    }
```

**File:** core/contracts/interfaces/IEndpoint.sol (L118-136)
```text
    struct SignedMintNlp {
        MintNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
    }

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

**File:** core/contracts/Verifier.sol (L378-385)
```text
            digest = keccak256(
                abi.encode(
                    keccak256(bytes(MINT_NLP_SIGNATURE)),
                    signedTx.tx.sender,
                    signedTx.tx.quoteAmount,
                    signedTx.tx.nonce
                )
            );
```

**File:** core/contracts/Verifier.sol (L391-398)
```text
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
