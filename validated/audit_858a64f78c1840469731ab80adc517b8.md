### Title
Lack of Slippage Protection in `burnNlp`/`mintNlp` — Sequencer-Provided Oracle Price Is Unsigned, Users Receive Fewer Assets Than Expected — (`core/contracts/Clearinghouse.sol`)

---

### Summary

The `BurnNlp` and `MintNlp` transaction structs contain no minimum-output parameter. The `oraclePriceX18` that determines the actual exchange rate is appended by the sequencer **outside** the user's EIP-712 signature. Users have no on-chain mechanism to enforce a minimum acceptable quote amount (for burns) or minimum NLP amount (for mints). If the NLP oracle price moves unfavorably between signing and sequencer execution, users silently receive fewer assets than expected with no ability to revert.

---

### Finding Description

**Root cause — `oraclePriceX18` is not covered by the user's EIP-712 signature.**

The EIP-712 type string for `BurnNlp` is:

```
"BurnNlp(bytes32 sender,uint128 nlpAmount,uint64 nonce)"
``` [1](#0-0) 

The user signs only `{sender, nlpAmount, nonce}`. The `oraclePriceX18` field lives in `SignedBurnNlp` **outside** the inner `BurnNlp tx` struct and is therefore not part of the digest:

```solidity
struct SignedBurnNlp {
    BurnNlp tx;
    bytes signature;
    int128 oraclePriceX18;       // ← NOT signed by user
    int128[] nlpPoolRebalanceX18;
}
``` [2](#0-1) 

The `Verifier.computeDigest` confirms this — only the inner `tx` fields are hashed:

```solidity
digest = keccak256(abi.encode(
    keccak256(bytes(BURN_NLP_SIGNATURE)),
    signedTx.tx.sender,
    signedTx.tx.nlpAmount,
    signedTx.tx.nonce          // oraclePriceX18 absent
));
``` [3](#0-2) 

**How the exchange rate is applied in `burnNlp`:**

```solidity
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
``` [4](#0-3) 

There is no `require(quoteAmount >= minQuoteAmount)` guard. The user receives whatever `nlpAmount * oraclePriceX18 - burnFee` computes to at execution time.

**Same structural flaw in `mintNlp`:**

```solidity
int128 nlpAmount = quoteAmount.div(oraclePriceX18);
``` [5](#0-4) 

The `MintNlp` struct also contains no `minNlpAmount` field, and `oraclePriceX18` is equally unsigned:

```
"MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce)"
``` [6](#0-5) 

**Execution path:**

`EndpointTx.sol` decodes the sequencer-submitted `SignedBurnNlp`, validates the user's signature over the inner `tx` only, then passes the unsigned `oraclePriceX18` directly to `clearinghouse.burnNlp`:

```solidity
priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
clearinghouse.burnNlp(
    signedTx.tx,
    signedTx.oraclePriceX18,   // sequencer-supplied, not user-signed
    nlpPools,
    signedTx.nlpPoolRebalanceX18
);
``` [7](#0-6) 

---

### Impact Explanation

A user who signs a `BurnNlp` for `X` NLP tokens while the NLP price is `P` expects to receive approximately `X * P` quote tokens. If the NLP price drops to `P'` before the sequencer includes the transaction in a batch, the user receives `X * P'` quote tokens instead — potentially far less — with no on-chain recourse. The user's signed intent (`nlpAmount`) is honoured, but the economic outcome is silently worse than expected.

The same applies symmetrically to `mintNlp`: a user spending `Y` quote tokens expects `Y / P` NLP tokens; if the price rises to `P'`, they receive `Y / P'` NLP tokens instead.

The corrupted state delta is the user's `QUOTE_PRODUCT_ID` balance (for burns) or `NLP_PRODUCT_ID` balance (for mints) being credited with fewer units than the user anticipated when signing.

---

### Likelihood Explanation

Medium. The sequencer processes transactions asynchronously. NLP price (`oraclePriceX18`) is updated via `UpdatePrice` transactions that the sequencer also controls. Any latency between a user signing and the sequencer executing — during which the NLP price moves — produces this outcome without any malicious intent. The sequencer is trusted, but price volatility is a normal market condition, not an attack prerequisite.

---

### Recommendation

1. Add a `minQuoteAmountX18` field to `BurnNlp` (and include it in the EIP-712 type string and digest) so users can specify the minimum quote they are willing to accept. In `burnNlp`, add:
   ```solidity
   require(quoteAmount >= txn.minQuoteAmountX18, ERR_SLIPPAGE_TOO_HIGH);
   ```
2. Add a `minNlpAmount` field to `MintNlp` (similarly signed) and check it in `mintNlp` after computing `nlpAmount`.

This mirrors the EIP-4626 recommendation cited in the external report and uses the already-defined `ERR_SLIPPAGE_TOO_HIGH` error constant. [8](#0-7) 

---

### Proof of Concept

1. NLP oracle price is currently `1.0e18` (1 quote token per NLP token).
2. Alice signs `BurnNlp { sender: alice, nlpAmount: 1000e18, nonce: 5 }` expecting ~1000 quote tokens back.
3. Before the sequencer batches Alice's transaction, an `UpdatePrice` transaction sets `oraclePriceX18 = 0.5e18`.
4. The sequencer submits Alice's `BurnNlp` with `oraclePriceX18 = 0.5e18`.
5. `burnNlp` computes `quoteAmount = 1000e18 * 0.5e18 / 1e18 = 500e18`, deducts burn fee, and credits Alice ~499.5 quote tokens.
6. Alice loses ~500 quote tokens relative to her expectation at signing time. No revert occurs; no minimum-output check exists.

### Citations

**File:** core/contracts/Verifier.sol (L26-27)
```text
    string internal constant MINT_NLP_SIGNATURE =
        "MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce)";
```

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

**File:** core/contracts/Clearinghouse.sol (L466-466)
```text
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);
```

**File:** core/contracts/Clearinghouse.sol (L502-504)
```text
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
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

**File:** core/contracts/common/Errors.sol (L90-90)
```text
string constant ERR_SLIPPAGE_TOO_HIGH = "STH";
```
