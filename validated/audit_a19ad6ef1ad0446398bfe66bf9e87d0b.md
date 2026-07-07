### Title
Unsigned `oraclePriceX18` in `MintNlp`/`BurnNlp` EIP-712 Digest Allows Sequencer to Manipulate NLP Execution Price Without User Consent — (File: `core/contracts/Verifier.sol`)

---

### Summary

The EIP-712 digest for `MintNlp` and `BurnNlp` transactions omits the sequencer-supplied `oraclePriceX18` and `nlpPoolRebalanceX18` fields. These fields are the primary determinants of how many NLP tokens a user receives (mint) or how much quote they receive (burn). Because they are excluded from the signed hash, a user's signature provides no cryptographic commitment to the price at which their operation executes — the exact on-chain analog of authorizing a transaction without seeing its parameters.

---

### Finding Description

In `Verifier.sol`, `computeDigest` for `MintNlp` hashes only `sender`, `quoteAmount`, and `nonce`:

```solidity
string internal constant MINT_NLP_SIGNATURE =
    "MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce)";
...
digest = keccak256(
    abi.encode(
        keccak256(bytes(MINT_NLP_SIGNATURE)),
        signedTx.tx.sender,
        signedTx.tx.quoteAmount,
        signedTx.tx.nonce
    )
);
``` [1](#0-0) [2](#0-1) 

The `SignedMintNlp` struct, however, carries two additional sequencer-supplied fields that are **not** included in the digest:

```solidity
struct SignedMintNlp {
    MintNlp tx;
    bytes signature;
    int128 oraclePriceX18;        // ← NOT in digest
    int128[] nlpPoolRebalanceX18; // ← NOT in digest
}
``` [3](#0-2) 

In `EndpointTx.sol`, `processTransactionImpl` uses both unsigned fields directly to execute the mint:

```solidity
priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
clearinghouse.mintNlp(
    signedTx.tx,
    signedTx.oraclePriceX18,       // price not committed to by user
    nlpPools,
    signedTx.nlpPoolRebalanceX18   // rebalance not committed to by user
);
``` [4](#0-3) 

The identical structural flaw exists for `BurnNlp`: [5](#0-4) [6](#0-5) 

The `BurnNlp` struct also carries unsigned `oraclePriceX18` and `nlpPoolRebalanceX18`: [7](#0-6) 

---

### Impact Explanation

`oraclePriceX18` is the NLP token price used to convert between quote and NLP shares. For `MintNlp`, an inflated `oraclePriceX18` causes the user to receive fewer NLP tokens per unit of `quoteAmount` deposited. For `BurnNlp`, a deflated `oraclePriceX18` causes the user to receive less quote per unit of `nlpAmount` burned. In both cases the user's collateral is transferred at an off-market rate while their signature — which commits only to the amount and nonce — remains cryptographically valid. The corrupted state delta is the user's NLP token balance (mint) or quote balance (burn), with the difference accruing to the NLP pool or being extracted by the sequencer.

---

### Likelihood Explanation

Exploitation requires a compromised or malicious sequencer, since `submitTransactionsChecked` enforces `msg.sender == sequencer`. The sequencer is a privileged off-chain entity. However, the EIP-712 scheme exists precisely to give users cryptographic guarantees independent of sequencer honesty. The incomplete digest defeats that guarantee entirely for these two transaction types: a user who signs a `MintNlp` or `BurnNlp` request has no on-chain protection against price manipulation, even if they inspect the digest they are signing. The root cause is a structural flaw in `Verifier.sol`, not merely an operational risk.

---

### Recommendation

Add `oraclePriceX18` and `nlpPoolRebalanceX18` to the EIP-712 type strings and digest encoding for both `MintNlp` and `BurnNlp` in `Verifier.sol`:

```solidity
string internal constant MINT_NLP_SIGNATURE =
    "MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce,int128 oraclePriceX18)";
```

Include `oraclePriceX18` in the `keccak256(abi.encode(...))` call for both transaction types. Dynamic arrays such as `nlpPoolRebalanceX18` should be committed to via their `keccak256` hash per EIP-712 encoding rules. This ensures the user's signature cryptographically binds the price at which their NLP operation executes, matching the intent of the EIP-712 authorization model.

---

### Proof of Concept

1. User constructs a `MintNlp` with `quoteAmount = 1000e18` and signs the digest — which commits only to `(sender, 1000e18, nonce)`.
2. Compromised sequencer wraps the signed transaction into `SignedMintNlp` and sets `oraclePriceX18 = 2 × fair_price`.
3. Sequencer calls `submitTransactionsChecked` with this payload.
4. `EndpointTx.processTransactionImpl` calls `validateSignedTx` — signature check passes because `oraclePriceX18` is not in the digest.
5. `clearinghouse.mintNlp` executes at `2 × fair_price`, minting only half the expected NLP tokens.
6. User loses ~50% of their expected NLP allocation with no on-chain recourse; the sequencer's manipulation is indistinguishable from a legitimate submission. [8](#0-7) [9](#0-8)

### Citations

**File:** core/contracts/Verifier.sol (L26-27)
```text
    string internal constant MINT_NLP_SIGNATURE =
        "MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce)";
```

**File:** core/contracts/Verifier.sol (L321-325)
```text
    function computeDigest(
        IEndpoint.TransactionType txType,
        bytes calldata transactionBody
    ) public pure returns (bytes32) {
        bytes32 digest;
```

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

**File:** core/contracts/interfaces/IEndpoint.sol (L118-123)
```text
    struct SignedMintNlp {
        MintNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
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
