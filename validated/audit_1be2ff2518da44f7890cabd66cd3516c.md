### Title
Sequencer-Controlled `oraclePriceX18` in `MintNlp`/`BurnNlp` Is Excluded from User's EIP-712 Signature, Enabling Unchecked NLP Accounting Manipulation — (`core/contracts/Verifier.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

The `oraclePriceX18` field embedded in `SignedMintNlp` and `SignedBurnNlp` is **not covered by the user's EIP-712 signature**. The sequencer appends this value freely when processing NLP mint/burn transactions. Because `nlpAmount = quoteAmount / oraclePriceX18` (mint) and `quoteAmount = nlpAmount * oraclePriceX18` (burn), the sequencer can unilaterally determine how many NLP tokens a user receives for their quote, or how much quote a user receives when burning NLP — with no on-chain constraint tying the price to the user's intent.

---

### Finding Description

In `Verifier.sol`, the EIP-712 type strings and digest computation for `MintNlp` and `BurnNlp` only commit to `sender`, `quoteAmount`/`nlpAmount`, and `nonce`:

```
"MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce)"
"BurnNlp(bytes32 sender,uint128 nlpAmount,uint64 nonce)"
``` [1](#0-0) 

The `computeDigest` function for `MintNlp` hashes only the inner `MintNlp` struct fields, explicitly excluding `oraclePriceX18` and `nlpPoolRebalanceX18`: [2](#0-1) 

The same exclusion applies to `BurnNlp`: [3](#0-2) 

Yet in `EndpointTx.sol`, the sequencer freely populates `signedTx.oraclePriceX18` from the submitted transaction bytes and passes it directly to `clearinghouse.mintNlp` / `clearinghouse.burnNlp`: [4](#0-3) [5](#0-4) 

In `Clearinghouse.mintNlp`, the NLP amount minted is computed as:

```solidity
int128 nlpAmount = quoteAmount.div(oraclePriceX18);
``` [6](#0-5) 

In `Clearinghouse.burnNlp`, the quote returned is:

```solidity
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
``` [7](#0-6) 

Because `oraclePriceX18` is not part of the signed message, the sequencer can submit any price value alongside a validly-signed user transaction. There is no on-chain staleness check, no price bound, and no user-committed slippage tolerance enforced at the contract level.

---

### Impact Explanation

**For `mintNlp`:** If the sequencer inflates `oraclePriceX18`, the user receives fewer NLP tokens for their `quoteAmount`. The user's quote is debited in full while their NLP credit is reduced proportionally. This is a direct, quantifiable asset loss for the minting user and a corresponding dilution benefit to existing NLP holders.

**For `burnNlp`:** If the sequencer deflates `oraclePriceX18`, the user receives less quote back for their NLP tokens. The NLP is burned in full while the quote returned is reduced. This is a direct asset loss for the burning user.

The corrupted state delta is: `spotEngine.balance[NLP_PRODUCT_ID][txn.sender]` and `spotEngine.balance[QUOTE_PRODUCT_ID][txn.sender]` are set to values that do not reflect the fair exchange rate the user intended when signing. [8](#0-7) [9](#0-8) 

---

### Likelihood Explanation

The sequencer is a privileged but not fully trustless component of the Nado protocol. The structural absence of `oraclePriceX18` from the user's signature means there is **no cryptographic enforcement** preventing a sequencer from using an off-market price. Any sequencer compromise, sequencer key rotation failure, or protocol upgrade that introduces a new sequencer operator immediately exposes all pending `MintNlp`/`BurnNlp` transactions to this manipulation. The user has no way to specify a minimum NLP amount or maximum price in their signed intent, and the contract enforces no bound.

---

### Recommendation

Include `oraclePriceX18` (and optionally a user-specified slippage bound such as `minNlpAmount` for mints or `minQuoteAmount` for burns) in the EIP-712 type string and digest computation for both `MintNlp` and `BurnNlp`. This ensures the sequencer cannot deviate from the price the user committed to at signing time.

Updated type strings would be:
```
"MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce,int128 oraclePriceX18)"
"BurnNlp(bytes32 sender,uint128 nlpAmount,uint64 nonce,int128 oraclePriceX18)"
``` [1](#0-0) 

---

### Proof of Concept

1. User signs a `MintNlp` intent: `{sender: alice, quoteAmount: 1000e18, nonce: 5}`. The fair NLP price is `1.00` (i.e., `oraclePriceX18 = 1e18`), so the user expects `1000` NLP tokens.
2. The sequencer constructs a `SignedMintNlp` transaction with `oraclePriceX18 = 2e18` (double the fair price) and the user's valid signature over `{sender, quoteAmount, nonce}`.
3. `validateSignedTx` passes because the digest only covers `{sender, quoteAmount, nonce}` — the inflated price is not in scope.
4. `clearinghouse.mintNlp` executes: `nlpAmount = 1000e18 / 2e18 = 500`. Alice receives only `500` NLP tokens instead of `1000`, while her full `1000e18` quote is debited.
5. The `500` NLP shortfall represents a permanent loss: Alice cannot recover the missing NLP tokens, and the `N_ACCOUNT` retains the corresponding NLP balance. [2](#0-1) [10](#0-9)

### Citations

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

**File:** core/contracts/EndpointTx.sol (L554-573)
```text
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

**File:** core/contracts/Clearinghouse.sol (L502-504)
```text
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
