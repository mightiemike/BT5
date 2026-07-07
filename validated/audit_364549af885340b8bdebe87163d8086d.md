### Title
Missing Expiration/Deadline in `MintNlp` and `BurnNlp` Signed Transactions Allows Stale Execution at Unfavorable Oracle Prices — (`core/contracts/EndpointTx.sol`, `core/contracts/interfaces/IEndpoint.sol`)

---

### Summary

The `MintNlp` and `BurnNlp` user-signed transaction structs contain no expiration or deadline field. Unlike `Order`, which carries an `expiration` that is validated on-chain in `_validateOrder()`, a user's signed `MintNlp` or `BurnNlp` commitment can be held and executed by the sequencer at any future time. Because the oracle price (`oraclePriceX18`) is supplied by the sequencer at execution time and is **not covered by the user's signature**, the user has no on-chain protection against execution at a price that has moved significantly against them.

---

### Finding Description

The `Order` struct includes an `expiration` field:

```solidity
struct Order {
    bytes32 sender;
    int128 priceX18;
    int128 amount;
    uint64 expiration;   // ← deadline enforced on-chain
    uint64 nonce;
    uint128 appendix;
}
``` [1](#0-0) 

This expiration is checked in `_validateOrder()`:

```solidity
!_expired(order.expiration)
``` [2](#0-1) 

where `_expired` is:

```solidity
function _expired(uint64 expiration) internal view returns (bool) {
    return expiration <= getOracleTime();
}
``` [3](#0-2) 

By contrast, the `MintNlp` and `BurnNlp` structs that users sign contain **no expiration field**:

```solidity
struct MintNlp {
    bytes32 sender;
    uint128 quoteAmount;
    uint64 nonce;          // ← only replay protection, no deadline
}

struct BurnNlp {
    bytes32 sender;
    uint128 nlpAmount;
    uint64 nonce;          // ← only replay protection, no deadline
}
``` [4](#0-3) 

The sequencer-supplied fields `oraclePriceX18` and `nlpPoolRebalanceX18` are part of `SignedMintNlp`/`SignedBurnNlp` but are **not covered by the user's EIP-712 signature** — they are appended by the sequencer at submission time:

```solidity
struct SignedMintNlp {
    MintNlp tx;
    bytes signature;
    int128 oraclePriceX18;          // sequencer-supplied, not signed by user
    int128[] nlpPoolRebalanceX18;   // sequencer-supplied, not signed by user
}
``` [5](#0-4) 

When `MintNlp` is processed in `EndpointTx.processTransactionImpl()`, `validateSignedTx` only validates the `MintNlp` struct (sender + nonce), and then `clearinghouse.mintNlp()` is called with the sequencer-provided oracle price:

```solidity
validateSignedTx(signedTx.tx.sender, signedTx.tx.nonce, transaction, signedTx.signature, true);
chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
clearinghouse.mintNlp(signedTx.tx, signedTx.oraclePriceX18, nlpPools, signedTx.nlpPoolRebalanceX18);
``` [6](#0-5) 

The same pattern applies to `BurnNlp`: [7](#0-6) 

---

### Impact Explanation

A user who signs a `MintNlp` transaction commits to depositing a fixed `quoteAmount` of quote tokens. The NLP tokens they receive are determined by `oraclePriceX18` at execution time. If the oracle price of NLP has risen significantly since the user signed (meaning NLP is more expensive), the user receives far fewer NLP tokens per unit of quote than they expected. There is no on-chain mechanism for the user to say "execute this before time T or reject it."

The same applies in reverse for `BurnNlp`: a user commits to burning a fixed `nlpAmount` but the quote tokens received depend on the oracle price at execution time. If the oracle price has fallen, the user receives significantly less quote than anticipated.

The corrupted state delta is: **the user's quote balance (for MintNlp) or NLP balance (for BurnNlp) is reduced by a fixed amount, while the corresponding output is determined by a price the user never agreed to and cannot bound.**

---

### Likelihood Explanation

The sequencer processes transactions in batches. Under normal operation, a user submits a signed `MintNlp` off-chain and expects near-immediate execution. However, if the sequencer is congested, restarted, or experiences any processing delay, the signed transaction can sit in the sequencer's queue for an extended period. NLP oracle prices can move materially during this window. The user has no on-chain recourse because the nonce only prevents replay — it does not prevent delayed first execution. This is a realistic scenario on any L2 under load (e.g., during high-activity events on Ink Chain).

---

### Recommendation

Add an `expiration` field to `MintNlp` and `BurnNlp`, mirroring the pattern already used in `Order`:

```solidity
struct MintNlp {
    bytes32 sender;
    uint128 quoteAmount;
    uint64 expiration;   // add this
    uint64 nonce;
}

struct BurnNlp {
    bytes32 sender;
    uint128 nlpAmount;
    uint64 expiration;   // add this
    uint64 nonce;
}
```

In `EndpointTx.processTransactionImpl()`, before calling `clearinghouse.mintNlp()` / `clearinghouse.burnNlp()`, add:

```solidity
require(!_isExpired(signedTx.tx.expiration), ERR_EXPIRED);
```

using the same `_expired()` logic already present in `OffchainExchange`. This gives users the same deadline protection that order signers already have.

---

### Proof of Concept

1. Alice signs a `MintNlp` transaction committing to deposit 10,000 USDC worth of quote tokens to receive NLP at the current oracle price of $1.00/NLP (expecting ~10,000 NLP).
2. The sequencer experiences a processing backlog. Alice's signed transaction sits in the queue for several hours.
3. During this time, NLP oracle price rises to $2.00/NLP due to strong protocol performance.
4. The sequencer eventually processes Alice's transaction with `oraclePriceX18 = 2e18`.
5. Alice receives only ~5,000 NLP instead of the ~10,000 she expected — a 50% shortfall — with no on-chain mechanism to have prevented this.
6. Alice had no way to set a deadline on her signed commitment; the nonce only prevented a second execution, not a delayed first one.

The root cause is confirmed at: [4](#0-3) [8](#0-7)

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

**File:** core/contracts/interfaces/IEndpoint.sol (L261-268)
```text
    struct Order {
        bytes32 sender;
        int128 priceX18;
        int128 amount;
        uint64 expiration;
        uint64 nonce;
        uint128 appendix;
    }
```

**File:** core/contracts/OffchainExchange.sol (L345-347)
```text
    function _expired(uint64 expiration) internal view returns (bool) {
        return expiration <= getOracleTime();
    }
```

**File:** core/contracts/OffchainExchange.sol (L457-468)
```text
        return
            ((order.priceX18 > 0) || _isTWAP(order.appendix)) &&
            (signedOrder.order.sender == N_ACCOUNT ||
                _checkSignature(
                    order.sender,
                    orderDigest,
                    linkedSigner,
                    signedOrder.signature
                )) &&
            // valid amount
            (order.amount != 0) &&
            !_expired(order.expiration);
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

**File:** core/contracts/EndpointTx.sol (L559-573)
```text
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
