### Title
No User-Controlled Minimum Output Bound in `BurnNlp` and `MintNlp` — (`File: core/contracts/Clearinghouse.sol`, `core/contracts/interfaces/IEndpoint.sol`)

---

### Summary

The `BurnNlp` and `MintNlp` operations compute oracle-price-dependent output amounts at execution time, but the user's EIP-712 signed message does not commit to the `oraclePriceX18` used, and neither transaction struct contains a `minQuoteAmount` or `minNlpAmount` field. The sequencer supplies `oraclePriceX18` as an unsigned field in the wrapper struct, meaning the user has no on-chain mechanism to bound the minimum value they receive from the operation.

---

### Finding Description

The `BurnNlp` struct contains only `{sender, nlpAmount, nonce}`: [1](#0-0) 

The `SignedBurnNlp` wrapper adds `oraclePriceX18` as a sequencer-supplied field outside the user's signed payload: [2](#0-1) 

The EIP-712 digest for `BurnNlp` covers only `sender`, `nlpAmount`, and `nonce` — `oraclePriceX18` is explicitly excluded: [3](#0-2) 

In `Clearinghouse.burnNlp`, the quote payout is computed directly from the sequencer-provided price with no lower-bound check: [4](#0-3) 

The same structural flaw exists symmetrically in `MintNlp`. The `MintNlp` struct has no `minNlpAmount` field: [5](#0-4) 

And `mintNlp` computes the NLP output purely from the sequencer-provided price: [6](#0-5) 

The sequencer passes `oraclePriceX18` into `clearinghouse.burnNlp` / `clearinghouse.mintNlp` directly: [7](#0-6) 

---

### Impact Explanation

For `BurnNlp`: a user burns a fixed `nlpAmount` of NLP tokens and receives `quoteAmount = nlpAmount * oraclePriceX18 - burnFee`. If the sequencer supplies a stale or depressed `oraclePriceX18`, the user receives substantially less quote than the current market value of the NLP burned. There is no on-chain check that `quoteAmount >= minQuoteAmount` because no such field exists in the user's signed message.

For `MintNlp`: a user pays a fixed `quoteAmount` and receives `nlpAmount = quoteAmount / oraclePriceX18`. If the sequencer supplies an inflated `oraclePriceX18`, the user receives far fewer NLP tokens than expected for the quote paid.

In both cases the corrupted state delta is the user's quote or NLP balance: the user loses real asset value with no recourse, and the discrepancy accrues to the NLP pool or insurance fund.

---

### Likelihood Explanation

The sequencer is the sole provider of `oraclePriceX18` in both `SignedMintNlp` and `SignedBurnNlp`. Because this field is not covered by the user's EIP-712 signature, the user cannot detect or reject an unfavorable price at the contract level. Price staleness can arise from normal sequencer latency during volatile market conditions without any malicious intent, and the user has no slippage protection to fall back on. The likelihood is moderate: it does not require key compromise, but it does require the sequencer to process the transaction at a price that diverges from the user's expectation.

---

### Recommendation

**Short term**: Add a `minQuoteAmount` field to the `BurnNlp` struct and a `minNlpAmount` field to the `MintNlp` struct. Include these fields in the EIP-712 signed digest. In `burnNlp`, add `require(quoteAmount >= txn.minQuoteAmount, ERR_SLIPPAGE)` after computing `quoteAmount`. In `mintNlp`, add `require(nlpAmount >= txn.minNlpAmount, ERR_SLIPPAGE)` after computing `nlpAmount`.

**Long term**: Ensure that every user-signed transaction whose output amount depends on a sequencer-supplied parameter commits to a user-controlled bound on that output in the signed digest, following the same pattern as Uniswap's `amountOutMinimum`.

---

### Proof of Concept

1. Alice signs `BurnNlp(sender=Alice, nlpAmount=1000e18, nonce=5)` when NLP trades at $10, expecting ~$10,000 quote minus burn fee.
2. The sequencer constructs `SignedBurnNlp` with `oraclePriceX18 = 1e18` ($1.00) — a stale or manipulated price.
3. `Clearinghouse.burnNlp` executes: `quoteAmount = 1000e18 * 1e18 / 1e18 = 1000e18` ($1,000). [4](#0-3) 
4. Alice's NLP balance is debited by `1000e18` and her quote balance is credited by `~$1,000` instead of `~$10,000`. [8](#0-7) 
5. No check in the contract rejects this execution because `BurnNlp` contains no `minQuoteAmount` field and `oraclePriceX18` is not part of Alice's signed digest. [3](#0-2)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L112-116)
```text
    struct MintNlp {
        bytes32 sender;
        uint128 quoteAmount;
        uint64 nonce;
    }
```

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

**File:** core/contracts/Clearinghouse.sol (L464-466)
```text
        require(txn.quoteAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);
```

**File:** core/contracts/Clearinghouse.sol (L502-504)
```text
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
```

**File:** core/contracts/Clearinghouse.sol (L511-515)
```text
        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);

        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
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
