### Title
`BurnNlp` Lacks Caller-Side Minimum-Receive Bound for Quote Settlement — (`core/contracts/Clearinghouse.sol`)

---

### Summary

The `BurnNlp` flow (`submitTransactionsChecked()` → `processTransactionImpl()` → `clearinghouse.burnNlp()`) computes the quote amount a user receives by multiplying `nlpAmount` by a sequencer-supplied `oraclePriceX18` that is **not covered by the user's EIP-712 signature**. The `BurnNlp` signed struct contains no `minQuoteAmount` field, so the user has no on-chain mechanism to enforce a minimum acceptable payout. The received quote can silently decrease relative to what the user expected when they signed.

---

### Finding Description

When a user burns NLP tokens, they sign a `BurnNlp` struct:

```solidity
struct BurnNlp {
    bytes32 sender;
    uint128 nlpAmount;   // only this is committed
    uint64 nonce;
}
``` [1](#0-0) 

The `SignedBurnNlp` wrapper appends `oraclePriceX18` and `nlpPoolRebalanceX18`, both supplied by the sequencer:

```solidity
struct SignedBurnNlp {
    BurnNlp tx;
    bytes signature;
    int128 oraclePriceX18;       // sequencer-supplied, NOT signed by user
    int128[] nlpPoolRebalanceX18;
}
``` [2](#0-1) 

The `Verifier` confirms that only `sender`, `nlpAmount`, and `nonce` enter the EIP-712 digest — `oraclePriceX18` is excluded:

```solidity
digest = keccak256(abi.encode(
    keccak256(bytes(BURN_NLP_SIGNATURE)),
    signedTx.tx.sender,
    signedTx.tx.nlpAmount,
    signedTx.tx.nonce          // oraclePriceX18 absent
));
``` [3](#0-2) 

Settlement in `Clearinghouse.burnNlp()` computes the payout entirely from the sequencer-provided price:

```solidity
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
``` [4](#0-3) 

The user then receives `quoteAmount` with no floor check against any user-specified bound:

```solidity
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
``` [5](#0-4) 

The same structural gap exists in `MintNlp`: the user signs a fixed `quoteAmount` to spend but receives `nlpAmount = quoteAmount / oraclePriceX18` NLP tokens with no `minNlpAmount` bound, and `oraclePriceX18` is likewise absent from the signed digest. [6](#0-5) 

---

### Impact Explanation

A user who signs a `BurnNlp` transaction expecting to receive, say, $1 000 USDC for 100 NLP tokens may receive materially less if the NLP oracle price has moved downward by the time the sequencer includes the transaction in a batch. Because `oraclePriceX18` is not committed in the signature, the user cannot enforce any minimum payout on-chain. The `burnFee` itself (`max(1e18, quoteAmount/1000)`) also scales with the oracle price, compounding the variance. The corrupted state is the user's quote balance in `SpotEngine`, which is debited by a larger-than-expected amount relative to the NLP surrendered.

---

### Likelihood Explanation

Every `BurnNlp` transaction is subject to this gap. NLP price is updated continuously via `UpdatePrice` transactions processed by the same sequencer pipeline. Any latency between the moment a user signs and the moment the sequencer executes the batch — even a few seconds during volatile markets — can shift `oraclePriceX18` enough to produce a meaningfully worse payout. No privileged access or adversarial actor is required; normal market price movement is sufficient.

---

### Recommendation

Add a `minQuoteAmount` field to the `BurnNlp` struct and include it in the EIP-712 digest. In `Clearinghouse.burnNlp()`, assert `quoteAmount >= txn.minQuoteAmount` before applying balance updates. Symmetrically, add `minNlpAmount` to `MintNlp` and assert `nlpAmount >= txn.minNlpAmount` after computing the mint quantity.

---

### Proof of Concept

1. User signs `BurnNlp { sender: alice, nlpAmount: 100e18, nonce: 5 }` when NLP oracle price is `10e18` (expecting ~`999e18` quote after burn fee).
2. Before the sequencer batches the transaction, a price update sets `oraclePriceX18 = 8e18`.
3. Sequencer submits `SignedBurnNlp` with `oraclePriceX18 = 8e18`. Signature validation passes because `oraclePriceX18` is not in the digest.
4. `Clearinghouse.burnNlp()` computes `quoteAmount = 100e18 * 8e18 / 1e18 = 800e18`, `burnFee = max(1e18, 800e18/1000) = 1e18 * 0.8 = 0.8e18`, net payout ≈ `799.2e18`.
5. Alice receives ~`799` USDC instead of the ~`999` USDC she anticipated — a ~20% shortfall — with no on-chain protection.

### Citations

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

**File:** core/contracts/Clearinghouse.sol (L502-504)
```text
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
```

**File:** core/contracts/Clearinghouse.sol (L515-516)
```text
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
```
