### Title
Sequencer-Supplied `oraclePriceX18` Is Excluded from EIP-712 Digest in NLP Mint/Burn, Enabling Stale-Price Execution Without User Consent — (`core/contracts/Verifier.sol`, `core/contracts/EndpointTx.sol`)

---

### Summary

The `oraclePriceX18` field embedded in `SignedMintNlp` and `SignedBurnNlp` is never included in the EIP-712 digest that the user signs. The sequencer supplies this price value freely at submission time, with no on-chain staleness bound or user-committed slippage constraint. A user who signs a `MintNlp` or `BurnNlp` transaction has no cryptographic guarantee about the NLP price that will be applied to their operation.

---

### Finding Description

`SignedMintNlp` and `SignedBurnNlp` each carry an `oraclePriceX18` field alongside the user's inner transaction and signature:

```solidity
struct SignedMintNlp {
    MintNlp tx;
    bytes signature;
    int128 oraclePriceX18;      // ← sequencer-supplied, not user-signed
    int128[] nlpPoolRebalanceX18;
}
``` [1](#0-0) 

In `Verifier.computeDigest()`, the EIP-712 digest for `MintNlp` covers only `sender`, `quoteAmount`, and `nonce`:

```solidity
string internal constant MINT_NLP_SIGNATURE =
    "MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce)";
``` [2](#0-1) 

```solidity
digest = keccak256(abi.encode(
    keccak256(bytes(MINT_NLP_SIGNATURE)),
    signedTx.tx.sender,
    signedTx.tx.quoteAmount,
    signedTx.tx.nonce
    // oraclePriceX18 is absent
));
``` [3](#0-2) 

The same omission applies to `BurnNlp`: [4](#0-3) 

In `EndpointTx.processTransactionImpl()`, the sequencer-supplied `oraclePriceX18` is written directly into the price map and forwarded to `Clearinghouse.mintNlp`/`burnNlp` without any staleness check or bound validation:

```solidity
priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
clearinghouse.mintNlp(signedTx.tx, signedTx.oraclePriceX18, nlpPools, ...);
``` [5](#0-4) 

```solidity
priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
clearinghouse.burnNlp(signedTx.tx, signedTx.oraclePriceX18, nlpPools, ...);
``` [6](#0-5) 

Inside `Clearinghouse.mintNlp`, the NLP amount minted is computed directly from this price:

```solidity
int128 nlpAmount = quoteAmount.div(oraclePriceX18);
``` [7](#0-6) 

And in `burnNlp`, the quote returned is:

```solidity
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
``` [8](#0-7) 

---

### Impact Explanation

**For `MintNlp`:** If a stale (inflated) `oraclePriceX18` is used, `nlpAmount = quoteAmount / oraclePriceX18` is smaller than it should be — the user pays full `quoteAmount` but receives fewer NLP tokens than the current market rate entitles them to.

**For `BurnNlp`:** If a stale (deflated) `oraclePriceX18` is used, `quoteAmount = nlpAmount * oraclePriceX18` is smaller — the user burns their NLP tokens but receives fewer quote tokens than the current market rate entitles them to.

In both cases the user's signature commits only to the amount they are depositing or burning, not to the price at which the conversion occurs. There is no on-chain slippage bound, no minimum-out check, and no staleness window enforced. The corrupted state delta is the NLP balance or quote balance of the user's subaccount.

**Impact: Medium** — direct asset loss to users proportional to the price deviation between signing time and execution time.

---

### Likelihood Explanation

The sequencer batches and sequences transactions. A `MintNlp` or `BurnNlp` signed by a user may sit in the queue while the NLP NAV moves. Because `oraclePriceX18` is not user-committed, the sequencer has no on-chain obligation to use a fresh price. Even an honest sequencer using a cached price from a prior block creates the same outcome as the reported vulnerability. The gap between user intent and execution price can be arbitrarily large during volatile periods.

**Likelihood: Medium** — no malice required; natural latency between signing and sequencing is sufficient.

---

### Recommendation

Include `oraclePriceX18` in the EIP-712 type hash and digest for both `MintNlp` and `BurnNlp`:

```solidity
string internal constant MINT_NLP_SIGNATURE =
    "MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce,int128 oraclePriceX18)";
```

This forces the user to commit to an acceptable price at signing time, giving them slippage protection equivalent to a `minOut` parameter. Alternatively, add an on-chain staleness check that rejects `oraclePriceX18` values that deviate beyond a configurable bound from the last sequencer-submitted `UpdatePrice` for `NLP_PRODUCT_ID`.

---

### Proof of Concept

1. User signs `MintNlp{sender=A, quoteAmount=1000e18, nonce=5}` when NLP NAV = 1.00 USDC, expecting ~1000 NLP tokens.
2. NLP NAV rises to 1.10 USDC before the sequencer processes the transaction.
3. Sequencer submits `SignedMintNlp{tx: ..., oraclePriceX18: 1.10e18, ...}` — the user's signature is valid because `oraclePriceX18` is not in the digest.
4. `Clearinghouse.mintNlp` computes `nlpAmount = 1000e18 / 1.10e18 ≈ 909 NLP`.
5. User receives ~91 fewer NLP tokens than they would have at the price they observed when signing, with no recourse.
6. The same logic applies in reverse for `BurnNlp` with a deflated price. [3](#0-2) [9](#0-8) [10](#0-9)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L118-123)
```text
    struct SignedMintNlp {
        MintNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
    }
```

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

**File:** core/contracts/EndpointTx.sol (L534-553)
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

**File:** core/contracts/Clearinghouse.sol (L502-502)
```text
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
```
