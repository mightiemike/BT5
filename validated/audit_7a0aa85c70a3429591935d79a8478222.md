### Title
No Slippage Protection on `mintNlp`/`burnNlp`: `oraclePriceX18` Is Sequencer-Supplied and Excluded from User Signature Digest — (`File: core/contracts/Clearinghouse.sol`, `core/contracts/Verifier.sol`)

---

### Summary

The `mintNlp` and `burnNlp` flows in Nado are the direct structural analogs to Renzo's `deposit()`/`withdraw()`. In both cases, the user signs a fixed input amount, but the oracle price that determines the output amount is not committed to in the user's signature. In Nado, `oraclePriceX18` is supplied by the sequencer as an unsigned field in `SignedMintNlp` and `SignedBurnNlp`, and is explicitly excluded from the EIP-712-style digest verified on-chain. The user has no mechanism to enforce a minimum output rate.

---

### Finding Description

When a user mints NLP tokens, they sign a `MintNlp` struct containing only `{sender, quoteAmount, nonce}`. The `oraclePriceX18` that determines how many NLP tokens they receive is a separate field in `SignedMintNlp`, appended by the sequencer, and is **not** included in the signed digest.

`Verifier.sol` confirms the digest for `MintNlp` is:

```solidity
digest = keccak256(abi.encode(
    keccak256(bytes(MINT_NLP_SIGNATURE)),
    signedTx.tx.sender,
    signedTx.tx.quoteAmount,
    signedTx.tx.nonce
));
```

`oraclePriceX18` is absent. [1](#0-0) 

The same pattern applies to `BurnNlp`: the user signs `{sender, nlpAmount, nonce}`, and `oraclePriceX18` is excluded from the digest. [2](#0-1) 

In `EndpointTx.sol`, after signature validation passes, the sequencer-supplied `signedTx.oraclePriceX18` is forwarded directly to `clearinghouse.mintNlp(...)` and `clearinghouse.burnNlp(...)` without any on-chain bound check: [3](#0-2) 

In `Clearinghouse.sol`, the NLP output for a mint is computed as:

```solidity
int128 nlpAmount = quoteAmount.div(oraclePriceX18);
```

A higher `oraclePriceX18` yields fewer NLP tokens for the same `quoteAmount`. [4](#0-3) 

For a burn, the quote output is:

```solidity
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
```

A lower `oraclePriceX18` yields less quote for the same `nlpAmount`. [5](#0-4) 

Neither `MintNlp` nor `BurnNlp` structs contain a `minOutputAmount` or `deadline` field. [6](#0-5) 

---

### Impact Explanation

**For `mintNlp`**: A user commits to spending a fixed `quoteAmount` of quote tokens. The number of NLP tokens they receive is `quoteAmount / oraclePriceX18`. Since `oraclePriceX18` is sequencer-supplied and not bound by the user's signature, the user can receive arbitrarily fewer NLP tokens than they expected at signing time. There is no on-chain floor on the output.

**For `burnNlp`**: A user commits to burning a fixed `nlpAmount`. The quote they receive is `nlpAmount * oraclePriceX18 - burnFee`. Since `oraclePriceX18` is sequencer-supplied and not bound by the user's signature, the user can receive arbitrarily less quote than they expected. There is no on-chain floor on the output.

The corrupted state delta is: `spotEngine` balance of `QUOTE_PRODUCT_ID` (for burn) or `NLP_PRODUCT_ID` (for mint) is updated at a rate the user never agreed to. [7](#0-6) 

---

### Likelihood Explanation

The sequencer is centralized and trusted in normal operation. However:

1. **Oracle price drift**: Between the moment a user signs a `MintNlp`/`BurnNlp` transaction and the moment the sequencer processes it, the oracle price can move. The user has no way to bound the acceptable price range in their signed message.
2. **Sequencer delay or reordering**: The sequencer can delay processing a signed transaction until the oracle price moves unfavorably, then execute it. The user cannot cancel because the nonce is already consumed.
3. **Compromised or malicious sequencer**: A compromised sequencer can supply any `oraclePriceX18` value — the on-chain contract performs no sanity check against a reference price or a user-specified bound.

The entry path is fully reachable by any user who calls `mintNlp` or `burnNlp` through the normal protocol flow. [8](#0-7) 

---

### Recommendation

Include `oraclePriceX18` (or a user-specified `minOutputAmount`) in the signed digest for both `MintNlp` and `BurnNlp`. Concretely:

1. Add a `minNlpAmount` field to `MintNlp` and a `minQuoteAmount` field to `BurnNlp`.
2. Include these fields in the `Verifier.sol` digest computation so they are cryptographically committed to by the user.
3. In `Clearinghouse.mintNlp` and `Clearinghouse.burnNlp`, assert that the computed output meets the user's minimum before applying balance updates.
4. Optionally add a `deadline` field to both structs and enforce it in `EndpointTx.sol`.

---

### Proof of Concept

1. User signs `MintNlp{sender=alice, quoteAmount=1000e18, nonce=5}` when NLP oracle price is `1.00`. Expected NLP output: `1000`.
2. Sequencer delays processing. Oracle price moves to `2.00`.
3. Sequencer submits the transaction with `oraclePriceX18 = 2e18`.
4. `Verifier.sol` validates the signature against `{sender, quoteAmount=1000e18, nonce=5}` — passes, because `oraclePriceX18` is not in the digest. [1](#0-0) 
5. `Clearinghouse.mintNlp` computes `nlpAmount = 1000e18 / 2e18 = 500`. Alice receives `500` NLP instead of `1000`. [4](#0-3) 
6. Alice's quote balance is debited by the full `1000e18` with no recourse. [9](#0-8) 

The same scenario applies symmetrically to `burnNlp` with a downward oracle price move reducing the quote output. [5](#0-4)

### Citations

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

**File:** core/contracts/Clearinghouse.sol (L465-466)
```text
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);
```

**File:** core/contracts/Clearinghouse.sol (L473-476)
```text
        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
```

**File:** core/contracts/Clearinghouse.sol (L502-504)
```text
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
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
