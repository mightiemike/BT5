### Title
Sequencer-Controlled `oraclePriceX18` in `MintNlp`/`BurnNlp` Is Excluded from User Signature, Enabling Output Manipulation - (File: `core/contracts/Clearinghouse.sol`, `core/contracts/Verifier.sol`)

---

### Summary

The `oraclePriceX18` parameter appended by the sequencer to `SignedMintNlp` and `SignedBurnNlp` transactions is never included in the EIP-712 digest that the user signs. The sequencer can freely supply any value for this field when submitting a transaction batch, directly controlling how many NLP tokens a user receives when minting, or how much quote a user receives when burning — with no on-chain slippage floor protecting the user.

---

### Finding Description

`SignedMintNlp` and `SignedBurnNlp` are composite structs: the inner `MintNlp`/`BurnNlp` struct is signed by the user, while `oraclePriceX18` and `nlpPoolRebalanceX18` are appended fields that the sequencer fills in at submission time.

In `Verifier.computeDigest`, the digest for `MintNlp` covers only `sender`, `quoteAmount`, and `nonce`:

```solidity
digest = keccak256(abi.encode(
    keccak256(bytes(MINT_NLP_SIGNATURE)),
    signedTx.tx.sender,
    signedTx.tx.quoteAmount,
    signedTx.tx.nonce
));
``` [1](#0-0) 

And for `BurnNlp`, only `sender`, `nlpAmount`, and `nonce`:

```solidity
digest = keccak256(abi.encode(
    keccak256(bytes(BURN_NLP_SIGNATURE)),
    signedTx.tx.sender,
    signedTx.tx.nlpAmount,
    signedTx.tx.nonce
));
``` [2](#0-1) 

`oraclePriceX18` is a separate field in the outer `SignedMintNlp`/`SignedBurnNlp` struct, entirely outside the signed payload: [3](#0-2) 

In `Clearinghouse.mintNlp`, this unsigned `oraclePriceX18` directly determines the NLP amount minted to the user:

```solidity
int128 nlpAmount = quoteAmount.div(oraclePriceX18);
``` [4](#0-3) 

In `Clearinghouse.burnNlp`, it directly determines the quote amount returned to the user:

```solidity
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
``` [5](#0-4) 

There is no minimum-output guard anywhere in either function that the user can enforce on-chain.

---

### Impact Explanation

**`mintNlp` attack:** A malicious sequencer inflates `oraclePriceX18` (e.g., 10× the fair price). The user's full `quoteAmount` is debited from their spot balance, but they receive only `1/10` of the NLP tokens they should. The remaining NLP value stays with `N_ACCOUNT` (the protocol pool), effectively transferring wealth from the minting user to existing NLP holders or the protocol.

**`burnNlp` attack:** A malicious sequencer deflates `oraclePriceX18` (e.g., to near zero). The user's full `nlpAmount` is burned, but they receive near-zero quote in return. The quote that should have been returned remains in the NLP pool subaccounts via `_applyNlpRebalance`.

In both cases the corrupted state delta is: `spotEngine.balance[QUOTE_PRODUCT_ID][user]` and `spotEngine.balance[NLP_PRODUCT_ID][user]` are set to values that do not correspond to the fair NLP price, with no recourse for the user. [6](#0-5) [7](#0-6) 

---

### Likelihood Explanation

The sequencer is the sole entity authorized to call `submitTransactions` / `processTransaction` on `Endpoint`. Every `MintNlp` and `BurnNlp` transaction passes through the sequencer before reaching the chain. The sequencer constructs the full `SignedMintNlp`/`SignedBurnNlp` calldata, including the unsigned `oraclePriceX18` field, with no on-chain constraint tying it to any external oracle or the `priceX18` mapping already stored in `Endpoint`. A compromised or malicious sequencer can exploit this on every NLP mint/burn without any on-chain detection. [8](#0-7) 

---

### Recommendation

Include `oraclePriceX18` in the EIP-712 signed digest for both `MintNlp` and `BurnNlp`, so the user commits to an acceptable price at signing time:

```solidity
// MINT_NLP_SIGNATURE should become:
// "MintNlp(bytes32 sender,uint128 quoteAmount,int128 oraclePriceX18,uint64 nonce)"
digest = keccak256(abi.encode(
    keccak256(bytes(MINT_NLP_SIGNATURE)),
    signedTx.tx.sender,
    signedTx.tx.quoteAmount,
    signedTx.oraclePriceX18,   // <-- add
    signedTx.tx.nonce
));
```

Alternatively, add a user-specified `minNlpOut` / `minQuoteOut` field to the inner signed struct and enforce it on-chain in `mintNlp`/`burnNlp`. Either approach closes the gap by making the output guarantee part of the user's signed intent.

---

### Proof of Concept

1. User signs `MintNlp{sender=alice, quoteAmount=1000e18, nonce=5}` and submits to the sequencer off-chain.
2. Sequencer constructs `SignedMintNlp{tx: <above>, signature: <alice's sig>, oraclePriceX18: 1000e18 /* 10× fair price of 100e18 */, nlpPoolRebalanceX18: [1000e18]}`.
3. Sequencer calls `Endpoint.submitTransactions(...)` with this crafted payload.
4. `EndpointTx` calls `validateSignedTx` — signature validates correctly because `oraclePriceX18` is not in the digest.
5. `Clearinghouse.mintNlp` executes: `nlpAmount = 1000e18 / 1000e18 = 1` NLP token instead of the fair `1000e18 / 100e18 = 10` NLP tokens.
6. Alice's quote balance is reduced by `1000e18`, but she receives `1` NLP token instead of `10`. The `9` NLP tokens remain with `N_ACCOUNT`. [9](#0-8) [10](#0-9)

### Citations

**File:** core/contracts/Verifier.sol (L26-29)
```text
    string internal constant MINT_NLP_SIGNATURE =
        "MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce)";
    string internal constant BURN_NLP_SIGNATURE =
        "BurnNlp(bytes32 sender,uint128 nlpAmount,uint64 nonce)";
```

**File:** core/contracts/Verifier.sol (L373-385)
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
