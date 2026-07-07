### Title
No Slippage Protection in `mintNlp` and `burnNlp` Allows Users to Receive Less Than Expected - (File: `core/contracts/Clearinghouse.sol`)

### Summary
The `mintNlp` and `burnNlp` functions in `Clearinghouse.sol` accept a user-signed amount (`quoteAmount` or `nlpAmount`) but apply an `oraclePriceX18` that is supplied by the sequencer at execution time and is **not committed to by the user's signature**. There is no `minNlpAmount` or `minQuoteAmount` slippage guard. If the NLP oracle price moves between when the user signs and when the sequencer executes, the user receives materially fewer tokens than expected with no on-chain protection.

---

### Finding Description

The `MintNlp` struct that the user signs contains only `sender`, `quoteAmount`, and `nonce`: [1](#0-0) 

The `SignedMintNlp` wrapper — which the sequencer constructs and submits on-chain — adds `oraclePriceX18` and `nlpPoolRebalanceX18` as separate fields outside the signed struct: [2](#0-1) 

Inside `mintNlp`, the NLP tokens minted are computed as:

```
nlpAmount = quoteAmount / oraclePriceX18
``` [3](#0-2) 

The user's `quoteAmount` is debited in full regardless of the resulting `nlpAmount`: [4](#0-3) 

There is no check of the form `require(nlpAmount >= minNlpAmount)`. Because `oraclePriceX18` is not part of the signed `MintNlp` struct, the user cannot bind the sequencer to a specific price at signing time, and no on-chain guard enforces a minimum output.

The symmetric issue exists in `burnNlp`: the user signs only `nlpAmount`, and the quote returned is `nlpAmount * oraclePriceX18 - burnFee`. A lower oracle price yields less quote with no floor: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

**`mintNlp`**: A user who signs a `MintNlp` transaction expecting to receive `Y` NLP tokens at price `P` will receive `Y' = quoteAmount / P'` tokens if the oracle price has risen to `P' > P` by execution time. The full `quoteAmount` is still debited. The user overpays per NLP token with no recourse.

**`burnNlp`**: A user who signs a `BurnNlp` transaction expecting to receive `X` quote tokens at price `P` will receive `X' = nlpAmount * P' - burnFee` if the oracle price has fallen to `P' < P` by execution time. The full `nlpAmount` is still burned. The user receives less quote than expected with no recourse.

In both cases the corrupted state delta is the user's quote or NLP balance — a direct, concrete asset loss.

---

### Likelihood Explanation

NLP oracle prices are volatile by nature (they reflect the NAV of the liquidity pool). The sequencer batches transactions and applies the current oracle price at execution time. In any period of market movement between a user signing and the sequencer executing, the price can shift materially. No adversarial actor is required — normal market conditions are sufficient to trigger the discrepancy. The `MintNlp` and `BurnNlp` flows are user-facing entrypoints reachable by any unprivileged caller.

---

### Recommendation

1. Add a `minNlpAmount` field to the `MintNlp` struct (signed by the user) and enforce it in `mintNlp`:
   ```solidity
   require(nlpAmount >= int128(txn.minNlpAmount), ERR_SLIPPAGE_TOO_HIGH);
   ```
2. Add a `minQuoteAmount` field to the `BurnNlp` struct (signed by the user) and enforce it in `burnNlp`:
   ```solidity
   require(quoteAmount >= int128(txn.minQuoteAmount), ERR_SLIPPAGE_TOO_HIGH);
   ```

Both fields must be part of the signed struct (not the outer `Signed*` wrapper) so the user's signature commits to the acceptable slippage bound. The error constant `ERR_SLIPPAGE_TOO_HIGH` already exists in the codebase: [7](#0-6) 

---

### Proof of Concept

**`mintNlp` scenario:**

1. NLP oracle price is `P = 1.00 USDC/NLP`. User signs `MintNlp { quoteAmount = 1000e18, nonce = N }` expecting `1000` NLP tokens.
2. Before the sequencer executes, the NLP NAV increases; sequencer uses `oraclePriceX18 = 1.10e18`.
3. `mintNlp` executes: `nlpAmount = 1000e18 / 1.10e18 ≈ 909 NLP`.
4. User's quote balance is debited by `1000` USDC; user receives only `909` NLP instead of `1000`.
5. No revert occurs. No minimum output was checked.

**`burnNlp` scenario:**

1. NLP oracle price is `P = 1.00 USDC/NLP`. User signs `BurnNlp { nlpAmount = 1000e18, nonce = N }` expecting `~999` USDC (after burn fee).
2. Before the sequencer executes, the NLP NAV decreases; sequencer uses `oraclePriceX18 = 0.90e18`.
3. `burnNlp` executes: `quoteAmount = 1000e18 * 0.90e18 / 1e18 - burnFee ≈ 899 USDC`.
4. User's NLP balance is debited by `1000` NLP; user receives only `~899` USDC instead of `~999`.
5. No revert occurs. No minimum output was checked. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L112-116)
```text
    struct MintNlp {
        bytes32 sender;
        uint128 quoteAmount;
        uint64 nonce;
    }
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

**File:** core/contracts/Clearinghouse.sol (L485-530)
```text
    function burnNlp(
        IEndpoint.BurnNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.nlpAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 nlpAmount = int128(txn.nlpAmount);
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, -quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] <= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);

        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
        }

        require(
            spotEngine.getBalance(NLP_PRODUCT_ID, txn.sender).amount >= 0,
            ERR_SUBACCT_HEALTH
        );
        // Burning NLP can decrease health if the burn fee exceeds the health improvement
        // from the withdrawal. This check prevents malicious actors from deliberately
        // creating unhealthy subaccounts through NLP burns.
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
```

**File:** core/contracts/common/Errors.sol (L90-90)
```text
string constant ERR_SLIPPAGE_TOO_HIGH = "STH";
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
