### Title
NLP Mint/Burn `oraclePriceX18` Excluded from User EIP-712 Commitment Allows Sequencer to Execute at Arbitrary Price — (`core/contracts/Verifier.sol`, `core/contracts/interfaces/IEndpoint.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

When a user signs a `MintNlp` or `BurnNlp` transaction, the EIP-712 digest they commit to does not include `oraclePriceX18`. The sequencer supplies this price value freely when submitting the batch. Because the NLP token amount minted and the quote amount returned on burn are both computed directly from `oraclePriceX18`, the user has no on-chain protection against receiving a worse-than-expected exchange rate.

---

### Finding Description

The `MintNlp` and `BurnNlp` inner structs that users sign contain only `sender`, `quoteAmount`/`nlpAmount`, and `nonce`:

```solidity
// IEndpoint.sol lines 112-136
struct MintNlp {
    bytes32 sender;
    uint128 quoteAmount;
    uint64 nonce;
}
struct SignedMintNlp {
    MintNlp tx;
    bytes signature;
    int128 oraclePriceX18;       // ← outside the signed struct
    int128[] nlpPoolRebalanceX18;
}
``` [1](#0-0) 

`Verifier.sol` confirms that `oraclePriceX18` is excluded from the EIP-712 digest for both transaction types:

```solidity
// Verifier.sol lines 378-398
// MintNlp digest: sender, quoteAmount, nonce only
digest = keccak256(abi.encode(
    keccak256(bytes(MINT_NLP_SIGNATURE)),
    signedTx.tx.sender,
    signedTx.tx.quoteAmount,
    signedTx.tx.nonce          // oraclePriceX18 absent
));
// BurnNlp digest: sender, nlpAmount, nonce only
digest = keccak256(abi.encode(
    keccak256(bytes(BURN_NLP_SIGNATURE)),
    signedTx.tx.sender,
    signedTx.tx.nlpAmount,
    signedTx.tx.nonce          // oraclePriceX18 absent
));
``` [2](#0-1) 

In `EndpointTx.sol`, the sequencer passes `signedTx.oraclePriceX18` directly into the clearinghouse without any user-bound constraint:

```solidity
// EndpointTx.sol lines 547-553 / 567-573
priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
clearinghouse.mintNlp(signedTx.tx, signedTx.oraclePriceX18, nlpPools, ...);
// and for burn:
priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
clearinghouse.burnNlp(signedTx.tx, signedTx.oraclePriceX18, nlpPools, ...);
``` [3](#0-2) [4](#0-3) 

In `Clearinghouse.sol`, the price directly determines the asset delta:

```solidity
// mintNlp — line 466
int128 nlpAmount = quoteAmount.div(oraclePriceX18);

// burnNlp — line 502
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
``` [5](#0-4) [6](#0-5) 

---

### Impact Explanation

**`mintNlp`:** A user signs a commitment to spend `quoteAmount` of collateral. The number of NLP tokens they receive is `quoteAmount / oraclePriceX18`. If the sequencer supplies an inflated `oraclePriceX18`, the user receives fewer NLP tokens than the fair-market rate — a direct, measurable asset loss with no on-chain recourse.

**`burnNlp`:** A user signs a commitment to burn `nlpAmount` of NLP. The quote returned is `nlpAmount * oraclePriceX18`. If the sequencer supplies a deflated `oraclePriceX18`, the user receives less collateral back than the fair-market rate — again a direct asset loss.

In both cases the corrupted state delta is the user's `QUOTE_PRODUCT_ID` balance and `NLP_PRODUCT_ID` balance in the spot engine. [7](#0-6) [8](#0-7) 

---

### Likelihood Explanation

The exploit path requires the sequencer to supply a manipulated `oraclePriceX18`. The Nado architecture uses a centralized sequencer for fast-mode submission, and the protocol explicitly provides a slow-mode path for censorship resistance — acknowledging that the sequencer is not unconditionally trusted. A compromised or malicious sequencer can silently extract value from every pending `MintNlp`/`BurnNlp` without the user having any signature-level protection. There is no on-chain bound, no user-specified slippage tolerance, and no deadline in the signed payload. [9](#0-8) 

---

### Recommendation

Include `oraclePriceX18` (or a user-specified `minNlpAmount`/`minQuoteAmount` slippage bound) inside the inner signed struct so it becomes part of the EIP-712 digest. For example:

```solidity
struct MintNlp {
    bytes32 sender;
    uint128 quoteAmount;
    uint64  nonce;
    int128  minNlpAmountX18;   // user-committed slippage floor
}

struct BurnNlp {
    bytes32 sender;
    uint128 nlpAmount;
    uint64  nonce;
    int128  minQuoteAmountX18; // user-committed slippage floor
}
```

The clearinghouse should then assert `nlpAmount >= txn.minNlpAmountX18` and `quoteAmount >= txn.minQuoteAmountX18` respectively, reverting if the sequencer-supplied price would violate the user's committed bound.

---

### Proof of Concept

1. User signs `MintNlp{sender=A, quoteAmount=1000e18, nonce=5}` at a time when the fair NLP price is `1.00` (expecting ~1000 NLP tokens).
2. Sequencer constructs `SignedMintNlp{tx=above, signature=valid, oraclePriceX18=2e18, nlpPoolRebalanceX18=[...]}`.
3. `Verifier.sol` validates the signature against `{sender, quoteAmount, nonce}` only — passes.
4. `Clearinghouse.mintNlp` computes `nlpAmount = 1000e18 / 2e18 = 500` — user receives 500 NLP instead of 1000.
5. User's `QUOTE_PRODUCT_ID` balance is debited 1000 quote; their `NLP_PRODUCT_ID` balance is credited only 500 NLP — a 50% loss with no on-chain protection. [10](#0-9) [5](#0-4)

### Citations

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

**File:** core/contracts/Clearinghouse.sol (L464-467)
```text
        require(txn.quoteAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);

```

**File:** core/contracts/Clearinghouse.sol (L473-477)
```text
        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
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

**File:** core/contracts/Clearinghouse.sol (L511-516)
```text
        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);

        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
```
