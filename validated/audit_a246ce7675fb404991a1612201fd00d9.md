### Title
No User-Specified Minimum Output in `mintNlp`/`burnNlp` Allows Execution at Arbitrarily Unfavorable Oracle Price — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

The `mintNlp` and `burnNlp` functions in `Clearinghouse.sol` compute the user's output token amount entirely from a sequencer-supplied `oraclePriceX18` that is **not part of the user's signed payload**. The user's signed structs (`MintNlp`, `BurnNlp`) contain no `minAmountOut` or price-bound field. There is no on-chain check that the received amount meets any user-defined minimum. `ERR_SLIPPAGE_TOO_HIGH` is defined in `Errors.sol` but is never enforced anywhere in the codebase.

---

### Finding Description

The user signs one of two structs:

```solidity
struct MintNlp {
    bytes32 sender;
    uint128 quoteAmount;   // only input amount is signed
    uint64 nonce;
}

struct BurnNlp {
    bytes32 sender;
    uint128 nlpAmount;     // only input amount is signed
    uint64 nonce;
}
```

Neither struct contains a `minNlpAmountOut` or `minQuoteAmountOut` field. The price used to compute the output is carried in the **outer** sequencer-submitted wrapper, which is not covered by the user's ECDSA signature:

```solidity
struct SignedMintNlp {
    MintNlp tx;
    bytes signature;
    int128 oraclePriceX18;          // sequencer-provided, NOT signed by user
    int128[] nlpPoolRebalanceX18;
}
```

In `Clearinghouse.mintNlp`, the NLP output is computed as:

```solidity
int128 nlpAmount = quoteAmount.div(oraclePriceX18);
```

A higher `oraclePriceX18` yields fewer NLP tokens for the same `quoteAmount`. There is no floor check on `nlpAmount`.

In `Clearinghouse.burnNlp`, the quote output is:

```solidity
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
```

A lower `oraclePriceX18` yields fewer quote tokens for the same `nlpAmount`. There is no floor check on `quoteAmount`.

The error constant `ERR_SLIPPAGE_TOO_HIGH` exists in `Errors.sol` but is never referenced in any contract logic, confirming the protection was anticipated but never implemented.

---

### Impact Explanation

**For `mintNlp`**: A user who signs a `MintNlp` authorising the spend of `quoteAmount` USDC can receive an arbitrarily small NLP balance if `oraclePriceX18` is inflated at execution time. The user's quote balance is debited in full regardless.

**For `burnNlp`**: A user who signs a `BurnNlp` authorising the burn of `nlpAmount` NLP can receive near-zero quote tokens if `oraclePriceX18` is deflated at execution time. The NLP balance is burned in full regardless.

In both cases the corrupted state delta is concrete: `spotEngine` balances for `NLP_PRODUCT_ID` and `QUOTE_PRODUCT_ID` are updated at the unfavorable price with no revert path for the user.

---

### Likelihood Explanation

The primary trigger is a sequencer that submits a `SignedMintNlp`/`SignedBurnNlp` with a manipulated `oraclePriceX18`. Because the sequencer is a trusted role, full exploitation requires sequencer compromise or malicious sequencer behaviour. However, even under an honest sequencer, significant NLP price movement between the moment the user signs and the moment the sequencer includes the transaction results in execution at a price the user never agreed to, with no on-chain recourse. The protocol provides no slow-mode path that lets the user supply their own price bound. Likelihood is **Medium**: sequencer-level trust is required for deliberate exploitation, but adverse execution under normal price volatility is reachable without any privileged action.

---

### Recommendation

Add a `minAmountOut` field to both user-signed structs and enforce it on-chain:

```solidity
struct MintNlp {
    bytes32 sender;
    uint128 quoteAmount;
    uint128 minNlpAmountOut;   // user-specified floor
    uint64 nonce;
}

struct BurnNlp {
    bytes32 sender;
    uint128 nlpAmount;
    uint128 minQuoteAmountOut; // user-specified floor
    uint64 nonce;
}
```

In `mintNlp`:
```solidity
require(uint128(nlpAmount) >= txn.minNlpAmountOut, ERR_SLIPPAGE_TOO_HIGH);
```

In `burnNlp`:
```solidity
require(uint128(quoteAmount) >= txn.minQuoteAmountOut, ERR_SLIPPAGE_TOO_HIGH);
```

This mirrors the recommendation in the external report and makes use of the already-defined `ERR_SLIPPAGE_TOO_HIGH` constant.

---

### Proof of Concept

1. User signs `MintNlp { sender: alice, quoteAmount: 1000e18, nonce: 1 }` when NLP price is `1.00`.
2. Before the sequencer includes the transaction, NLP price rises to `2.00` (or the sequencer deliberately uses `oraclePriceX18 = 2e18`).
3. Sequencer submits `SignedMintNlp { tx: <above>, oraclePriceX18: 2e18, ... }`.
4. `Clearinghouse.mintNlp` executes: `nlpAmount = 1000e18 / 2e18 = 500e18`.
5. Alice's quote balance is debited `1000e18`; she receives only `500` NLP instead of the `1000` she expected.
6. No revert occurs. `ERR_SLIPPAGE_TOO_HIGH` is never checked.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
