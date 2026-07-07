### Title
`mintNlp` Accepts Quote at 100% Oracle Price With No Minting Fee, Enabling Oracle-Deviation Arbitrage Against NLP Pool — (`core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.mintNlp` mints NLP tokens for a user at exactly 100% of the sequencer-supplied `oraclePriceX18` with **zero minting fee**. `burnNlp` charges a fee of `max(1, quoteAmount/1000)` (~0.1%). The `oraclePriceX18` field in `SignedMintNlp` is **not covered by the user's EIP-712 signature** — only `{sender, quoteAmount, nonce}` are signed. This creates the same asymmetric oracle-price exploitation surface as M-9: a user who mints NLP while the oracle price is stale-low can burn at the updated higher price, extracting value from the NLP pool with no fee barrier on entry.

---

### Finding Description

**Root cause 1 — No minting fee in `mintNlp`:**

`Clearinghouse.mintNlp` computes `nlpAmount = quoteAmount / oraclePriceX18` and credits the user with that many NLP tokens, deducting `quoteAmount` quote. No fee is applied. [1](#0-0) 

`burnNlp`, by contrast, deducts `burnFee = max(ONE, quoteAmount / 1000)` before returning quote to the user. [2](#0-1) 

**Root cause 2 — `oraclePriceX18` is outside the signed digest for `MintNlp`:**

`Verifier.computeDigest` for `MintNlp` hashes only `{sender, quoteAmount, nonce}`. The `oraclePriceX18` field present in `SignedMintNlp` is **not included**. [3](#0-2) 

The `SignedMintNlp` struct carries `oraclePriceX18` as a separate, unsigned field: [4](#0-3) 

`EndpointTx` reads `signedTx.oraclePriceX18` directly from the decoded struct and passes it to `clearinghouse.mintNlp` after signature validation — the validation never touches the price field: [5](#0-4) 

Because the oracle price is unsigned, the user has **no slippage protection** on the NLP amount they receive, and the sequencer can supply any price when batching the transaction on-chain.

---

### Impact Explanation

When the oracle price for NLP is stale-low relative to the true NLP pool value (a normal condition in any oracle system where price updates lag):

1. User mints NLP paying `Q` quote at stale price `P_low`: receives `Q / P_low` NLP tokens (more than fair share).
2. Oracle price updates to `P_high > P_low` (normal sequencer operation).
3. User burns NLP at `P_high`: receives `(Q / P_low) × P_high − fee` quote.
4. Net profit = `Q × (P_high / P_low − 1) − fee`.

This is profitable whenever the oracle price deviation exceeds the 0.1% burn fee — the same threshold identified in M-9. Repeated cycles drain the NLP pool's quote reserves, degrading collateral quality for all NLP holders. The value leaks to the arbitrageur.

---

### Likelihood Explanation

The NLP oracle price is sequencer-supplied and updated per-transaction. Any gap between consecutive price updates — including normal block-to-block latency — creates a window. Because `mintNlp` has zero entry cost and `burnNlp` only charges 0.1%, the required deviation to profit is minimal. A user who monitors oracle price updates and times mint/burn pairs around updates can extract value systematically. No privileged access is required beyond a normal subaccount.

---

### Recommendation

1. **Add a minting fee to `mintNlp`** at least equal to the burn fee (0.1%), mirroring the symmetric fee structure used in `burnNlp`. This eliminates the zero-cost entry that enables the arbitrage.
2. **Include `oraclePriceX18` in the `MintNlp` EIP-712 digest** so users commit to a specific price and receive slippage protection. This prevents the sequencer from substituting a stale price after the user has signed.

---

### Proof of Concept

```
// Precondition: NLP oracle price is P_low = 0.99e18 (stale, true value P_high = 1.00e18)

// Step 1: User signs MintNlp{sender, quoteAmount=1000e18, nonce}
//         Sequencer batches with oraclePriceX18 = 0.99e18 (stale)
//         mintNlp: nlpAmount = 1000e18 / 0.99e18 ≈ 1010.1 NLP  (no fee)

// Step 2: Sequencer updates oracle price to 1.00e18 (normal operation)

// Step 3: User signs BurnNlp{sender, nlpAmount=1010.1, nonce}
//         burnNlp: quoteAmount = 1010.1 × 1.00e18 = 1010.1e18
//                 burnFee = max(1, 1010.1e18 / 1000) ≈ 1.0101e18
//                 returned = 1010.1e18 − 1.0101e18 ≈ 1009.09e18

// Net profit ≈ 9.09 quote tokens on a 1000 quote deposit (~0.9%)
// Exceeds the 0.1% burn fee barrier; cycle repeats until pool is drained.
```

The signed digest for `MintNlp` covers only `{sender, quoteAmount, nonce}` — the `oraclePriceX18 = 0.99e18` used in Step 1 is never validated against the user's signature, confirming the unsigned price substitution path. [3](#0-2) [1](#0-0) [2](#0-1)

### Citations

**File:** core/contracts/Clearinghouse.sol (L464-477)
```text
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
```

**File:** core/contracts/Clearinghouse.sol (L502-504)
```text
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
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

**File:** core/contracts/interfaces/IEndpoint.sol (L118-123)
```text
    struct SignedMintNlp {
        MintNlp tx;
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
