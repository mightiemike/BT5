### Title
No Slippage Protection on NLP Mint/Burn — Oracle Price Not Covered by User Signature - (File: `core/contracts/Clearinghouse.sol`, `core/contracts/Verifier.sol`)

---

### Summary

The `mintNlp` and `burnNlp` flows in Nado lack any slippage protection. The `oraclePriceX18` that determines how many NLP tokens a user receives (or how much quote a user gets back) is **not included in the user's signed transaction**. The sequencer appends this value freely, and no minimum-output check exists on-chain. A user who submits a `MintNlp` or `BurnNlp` transaction has no guarantee that the execution price will match their expectation.

---

### Finding Description

The EIP-712 type strings for both operations are:

```
MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce)
BurnNlp(bytes32 sender,uint128 nlpAmount,uint64 nonce)
``` [1](#0-0) 

The `oraclePriceX18` field is present in `SignedMintNlp` and `SignedBurnNlp` but is **outside** the inner `tx` struct that the user signs: [2](#0-1) 

`computeDigest` in `Verifier.sol` decodes the full `SignedMintNlp` but only hashes the inner `tx` fields — `sender`, `quoteAmount`, `nonce` — explicitly excluding `oraclePriceX18`: [3](#0-2) 

In `EndpointTx.sol`, the sequencer supplies `signedTx.oraclePriceX18` directly and passes it to the clearinghouse without any user-enforced bound: [4](#0-3) 

In `Clearinghouse.mintNlp`, the NLP amount received is computed as `quoteAmount / oraclePriceX18` with no minimum NLP output check: [5](#0-4) 

In `Clearinghouse.burnNlp`, the quote returned is computed as `nlpAmount * oraclePriceX18` with no minimum quote output check: [6](#0-5) 

---

### Impact Explanation

**For `mintNlp`:** A user signs a transaction committing to spend `quoteAmount` of quote tokens. If the NLP oracle price rises between submission and sequencer execution, the user receives fewer NLP tokens than anticipated, with no on-chain recourse.

**For `burnNlp`:** A user signs a transaction committing to burn `nlpAmount` of NLP tokens. If the NLP oracle price falls between submission and sequencer execution, the user receives fewer quote tokens than anticipated. The NLP tokens are already burned — the loss is irreversible.

In both cases, the corrupted state delta is the user's quote or NLP balance: the user pays a fixed input but receives a variable, potentially much worse output, with no protection.

---

### Likelihood Explanation

NLP token price is oracle-driven and changes continuously with market conditions. The latency between a user submitting a signed transaction and the sequencer including it in a batch is non-trivial. Any price movement during that window directly harms the user. No special privileges or exploits are required — this is triggered by any ordinary `mintNlp` or `burnNlp` user.

---

### Recommendation

Add a user-controlled slippage bound to both signed structs:

- `MintNlp`: add `uint128 minNlpAmount` to the signed struct and enforce `nlpAmount >= minNlpAmount` in `Clearinghouse.mintNlp`.
- `BurnNlp`: add `uint128 minQuoteAmount` to the signed struct and enforce `quoteAmount >= minQuoteAmount` in `Clearinghouse.burnNlp`.

Both fields must be included in the EIP-712 type string and digest computation in `Verifier.computeDigest` so the user's signature covers the price bound.

---

### Proof of Concept

1. NLP oracle price is currently `1.00 USDC` per NLP token.
2. User signs `MintNlp { sender, quoteAmount: 1000e18, nonce }` expecting to receive `1000` NLP tokens.
3. Before the sequencer processes the transaction, the oracle price updates to `1.10 USDC`.
4. Sequencer submits `SignedMintNlp { tx: <user-signed>, oraclePriceX18: 1.10e18, ... }`.
5. `validateSignedTx` passes — the signature is valid because `oraclePriceX18` is not in the digest.
6. `nlpAmount = 1000e18 / 1.10e18 = ~909` NLP tokens are credited to the user.
7. User paid `1000 USDC` but received `~909` NLP tokens instead of `1000`. No check prevented this.

The same scenario applies to `burnNlp` in reverse: user burns a fixed `nlpAmount` but receives fewer quote tokens if the price drops before execution. [7](#0-6) [8](#0-7)

### Citations

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

**File:** core/contracts/EndpointTx.sol (L534-573)
```text
        } else if (txType == IEndpoint.TransactionType.MintNlp) {
            IEndpoint.SignedMintNlp memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedMintNlp)
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
            clearinghouse.mintNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
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
