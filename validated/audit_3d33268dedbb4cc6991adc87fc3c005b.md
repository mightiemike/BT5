### Title
`burnNlp` and `mintNlp` lack slippage protection: `oraclePriceX18` is excluded from user signature, enabling unfavorable execution - (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

`mintNlp` and `burnNlp` in `Clearinghouse.sol` are swap-like operations where a user commits to paying a fixed input amount but has no on-chain protection over the output amount. The exchange rate (`oraclePriceX18`) is sequencer-supplied and is **not** included in the EIP-712 digest the user signs. There is no minimum output amount field in either signed struct. A user can receive significantly fewer tokens than expected with no recourse.

---

### Finding Description

`mintNlp` computes the NLP tokens minted as:

```solidity
int128 nlpAmount = quoteAmount.div(oraclePriceX18);
```

`burnNlp` computes the quote tokens returned as:

```solidity
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
``` [1](#0-0) [2](#0-1) 

The `oraclePriceX18` is provided by the sequencer as part of `SignedMintNlp` / `SignedBurnNlp` and is passed directly to `clearinghouse.mintNlp` / `clearinghouse.burnNlp`:

```solidity
priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
clearinghouse.mintNlp(signedTx.tx, signedTx.oraclePriceX18, nlpPools, signedTx.nlpPoolRebalanceX18);
``` [3](#0-2) 

The EIP-712 type strings for both operations **exclude** `oraclePriceX18`:

```solidity
string internal constant MINT_NLP_SIGNATURE =
    "MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce)";
string internal constant BURN_NLP_SIGNATURE =
    "BurnNlp(bytes32 sender,uint128 nlpAmount,uint64 nonce)";
``` [4](#0-3) 

The digest computation confirms only `sender`, `quoteAmount`/`nlpAmount`, and `nonce` are hashed — `oraclePriceX18` is never committed to by the user: [5](#0-4) 

The signed structs themselves confirm the separation:

```solidity
struct MintNlp { bytes32 sender; uint128 quoteAmount; uint64 nonce; }
struct SignedMintNlp { MintNlp tx; bytes signature; int128 oraclePriceX18; int128[] nlpPoolRebalanceX18; }

struct BurnNlp { bytes32 sender; uint128 nlpAmount; uint64 nonce; }
struct SignedBurnNlp { BurnNlp tx; bytes signature; int128 oraclePriceX18; int128[] nlpPoolRebalanceX18; }
``` [6](#0-5) 

---

### Impact Explanation

**`burnNlp`:** A user signs a commitment to burn a fixed `nlpAmount`. The sequencer executes the transaction with an `oraclePriceX18` the user never agreed to. If the price is stale or lower than expected, the user receives `nlpAmount * oraclePriceX18 - burnFee` quote tokens — potentially far less than the market value of the NLP burned. The NLP is already debited; there is no rollback.

**`mintNlp`:** A user signs a commitment to pay a fixed `quoteAmount`. If `oraclePriceX18` is higher than expected, the user receives fewer NLP tokens per unit of quote paid. The quote is already debited.

In both cases the user has no field in their signed transaction to express a minimum acceptable output, and no on-chain check enforces one. [7](#0-6) [8](#0-7) 

---

### Likelihood Explanation

Every user who calls `mintNlp` or `burnNlp` is exposed. The sequencer queue introduces latency between signing and execution. During that window the NLP oracle price can move. Because `oraclePriceX18` is sequencer-supplied and unsigned, the user has no on-chain guarantee the price used at execution matches the price they observed when signing. No special attacker capability is required — normal market price movement during sequencer delay is sufficient to trigger the loss.

---

### Recommendation

Add a `minOutputAmount` field to both `MintNlp` and `BurnNlp` structs and include it in the EIP-712 digest. In `Clearinghouse.mintNlp`, assert `nlpAmount >= txn.minNlpAmount`. In `Clearinghouse.burnNlp`, assert `quoteAmount >= txn.minQuoteAmount`. This mirrors the standard AMM slippage guard pattern and ensures the user's signed intent bounds the worst-case execution price.

---

### Proof of Concept

1. User observes NLP oracle price = 1.00 USDC/NLP and signs `BurnNlp{sender, nlpAmount=1000e18, nonce}` expecting ~1000 USDC back.
2. Transaction enters the sequencer queue. NLP oracle price drops to 0.50 USDC/NLP before execution.
3. Sequencer executes with `oraclePriceX18 = 0.50e18`. `quoteAmount = 1000e18 * 0.50e18 / 1e18 - burnFee ≈ 499 USDC`.
4. User's 1000 NLP are burned; user receives ~499 USDC instead of ~999 USDC.
5. The user's signature was valid — it covered only `{sender, nlpAmount, nonce}` — so no revert occurs. [9](#0-8) [10](#0-9)

### Citations

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

**File:** core/contracts/interfaces/IEndpoint.sol (L112-136)
```text
    struct MintNlp {
        bytes32 sender;
        uint128 quoteAmount;
        uint64 nonce;
    }

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
