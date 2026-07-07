### Title
No Slippage Protection on NLP Mint and Burn — Oracle Price Not User-Signed (`core/contracts/Clearinghouse.sol`, `core/contracts/interfaces/IEndpoint.sol`)

---

### Summary

The `mintNlp` and `burnNlp` operations in the Nado protocol execute at an `oraclePriceX18` that is supplied by the sequencer at execution time and is **not included in the user's signed message**. Users have no way to specify a minimum NLP output (for minting) or a minimum quote output (for burning), leaving them exposed to unbounded adverse price movement between the time they sign and the time the sequencer executes their transaction.

---

### Finding Description

When a user mints NLP tokens, they sign a `MintNlp` struct containing only `sender`, `quoteAmount`, and `nonce`. The `oraclePriceX18` lives in the outer `SignedMintNlp` wrapper and is **excluded from the EIP-712 digest** the user signs: [1](#0-0) 

The `Verifier` confirms this: the digest for `MintNlp` commits only to `sender`, `quoteAmount`, and `nonce` — `oraclePriceX18` is absent: [2](#0-1) 

The sequencer injects `oraclePriceX18` at submission time and passes it directly to `clearinghouse.mintNlp`: [3](#0-2) 

Inside `mintNlp`, the NLP amount the user receives is computed as `quoteAmount / oraclePriceX18` with no floor check: [4](#0-3) 

The symmetric problem exists for `burnNlp`. The user signs only `sender`, `nlpAmount`, `nonce`: [5](#0-4) 

The digest excludes `oraclePriceX18`: [6](#0-5) 

Inside `burnNlp`, the quote the user receives is `nlpAmount * oraclePriceX18` with no minimum output check: [7](#0-6) 

In both cases the user's signature commits to the input amount only. The output amount is entirely determined by a sequencer-supplied price that the user cannot bound.

---

### Impact Explanation

**`mintNlp`:** A user signs a commitment to spend `quoteAmount` of quote tokens. If the NLP oracle price rises between signing and execution, `nlpAmount = quoteAmount / oraclePriceX18` shrinks proportionally. The user's quote balance is debited in full while they receive fewer NLP tokens than they intended — a direct, measurable asset loss relative to their signed intent.

**`burnNlp`:** A user signs a commitment to burn `nlpAmount` of NLP tokens. If the NLP oracle price falls between signing and execution, `quoteAmount = nlpAmount * oraclePriceX18` shrinks proportionally. The user's NLP balance is burned in full while they receive less quote than they intended — again a direct asset loss.

The corrupted state delta is the user's quote balance (mintNlp) or NLP balance (burnNlp): the user irreversibly loses value with no on-chain mechanism to reject the execution.

---

### Likelihood Explanation

NLP is a liquidity-provider token whose oracle price reflects the aggregate value of the underlying pool. During periods of market volatility the price can move materially within the sequencer's processing window. Because the sequencer batches and sequences transactions asynchronously, a non-trivial delay between user signing and on-chain execution is inherent to the protocol design. No sequencer compromise is required — honest sequencer behavior with a stale or recently-updated price is sufficient to trigger the loss.

---

### Recommendation

Add a user-controlled slippage bound to both structs:

- `MintNlp`: add a `uint128 minNlpAmount` field. In `mintNlp`, require `nlpAmount >= txn.minNlpAmount` after computing `nlpAmount = quoteAmount.div(oraclePriceX18)`.
- `BurnNlp`: add a `uint128 minQuoteAmount` field. In `burnNlp`, require `quoteAmount >= txn.minQuoteAmount` after computing `quoteAmount = nlpAmount.mul(oraclePriceX18) - burnFee`.

Both fields must be included in the EIP-712 digest inside `Verifier.sol` so the user's signature commits to the acceptable price range.

---

### Proof of Concept

1. NLP oracle price is currently `1.00 USDC` per NLP token.
2. User signs `MintNlp { sender: alice, quoteAmount: 1000e18, nonce: 5 }` expecting to receive ~1000 NLP.
3. Before the sequencer submits the batch, the NLP price rises to `2.00 USDC` per NLP (e.g., due to pool rebalancing or underlying asset appreciation).
4. Sequencer submits `SignedMintNlp { tx: <alice's signed struct>, oraclePriceX18: 2e18, ... }`.
5. `validateSignedTx` passes — the signature is valid because `oraclePriceX18` is not in the digest.
6. `mintNlp` executes: `nlpAmount = 1000e18 / 2e18 = 500`. Alice's quote balance is debited `1000e18` and she receives only `500` NLP tokens — half of what she expected — with no revert possible.

The same scenario applies to `burnNlp` with a falling oracle price: a user burning 1000 NLP at an expected price of `1.00` receives only `500` quote if the price has dropped to `0.50` by execution time.

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L112-123)
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
```

**File:** core/contracts/interfaces/IEndpoint.sol (L125-136)
```text
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
