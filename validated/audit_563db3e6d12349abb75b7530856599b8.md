### Title
Rounding to Zero in `mintNlp` Allows Quote Drain Without NLP Issuance — (File: `core/contracts/Clearinghouse.sol`)

### Summary
In `Clearinghouse.mintNlp`, the computed `nlpAmount` can silently truncate to zero due to integer division in `MathSD21x18.div`. When this occurs, the user's `quoteAmount` is fully deducted and redistributed to NLP pool subaccounts via `_applyNlpRebalance`, but zero NLP tokens are credited to the user. The protocol's accounting invariant — that every quote deducted in a mint corresponds to a proportional NLP issuance — is broken.

### Finding Description
In `Clearinghouse.sol` at line 466, `nlpAmount` is computed as:

```solidity
int128 nlpAmount = quoteAmount.div(oraclePriceX18);
```

`MathSD21x18.div` performs `(int256(x) * ONE_X18) / y` with integer truncation toward zero. When `quoteAmount * 1e18 < oraclePriceX18`, the result is `0`. No guard exists to reject this outcome before the state mutations proceed.

The function then unconditionally executes:

```solidity
spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);   // +0 NLP to user
spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);   // -0 NLP from N_ACCOUNT
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount); // quote IS deducted
_applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);     // quote IS distributed to pools
``` [1](#0-0) 

The `_validateNlpRebalance` check at line 468 enforces `sum(nlpPoolRebalanceX18) == quoteAmount`, so the full `quoteAmount` is always distributed to NLP pool subaccounts regardless of whether `nlpAmount` is zero. [2](#0-1) 

The truncation threshold is: `nlpAmount = 0` whenever `quoteAmount < oraclePriceX18 / 1e18`. For an NLP oracle price of `P` USDC (stored as `P * 1e18`), any `quoteAmount` below `P` raw units triggers the zero-mint path. At elevated NLP valuations (e.g., $1,000 NLP → threshold = 1,000 raw quote units), this threshold reaches economically non-trivial values depending on the quote token's decimals. [3](#0-2) 

### Impact Explanation
When `nlpAmount == 0`:
- The user's quote balance is reduced by `quoteAmount` (a real asset deduction).
- The NLP pool subaccounts receive `quoteAmount` in quote (a real asset gain for pool holders).
- The user receives zero NLP tokens.

This is a direct, unrecoverable transfer of value from the minting user to NLP pool holders, with no corresponding NLP issuance. The accounting invariant `nlpAmount * oraclePriceX18 == quoteAmount` is violated. The user cannot recover the lost quote because the transaction succeeds and the state is finalized on-chain. [4](#0-3) 

### Likelihood Explanation
The trigger requires `quoteAmount` to be below the rounding threshold. The user signs the `MintNlp` transaction including `quoteAmount`, so this is most likely to occur accidentally (e.g., a UI or integration submitting a dust amount, or a user testing with a minimal value). The sequencer submits the transaction but cannot forge the user's signature; however, the user's signed `quoteAmount` is the sole input. At higher NLP oracle prices, the threshold rises, increasing the probability of accidental triggering. Likelihood is **low** but non-zero and grows with NLP price appreciation. [5](#0-4) 

### Recommendation
Add a zero-check on `nlpAmount` immediately after its computation in `mintNlp`:

```solidity
int128 nlpAmount = quoteAmount.div(oraclePriceX18);
require(nlpAmount > 0, "ERR_ZERO_NLP_MINTED");
```

This mirrors the recommendation from the referenced Lido report and ensures no quote is consumed unless a positive NLP amount is issued. [6](#0-5) 

### Proof of Concept

**Setup:**
- NLP oracle price: `oraclePriceX18 = 2e18` (2 USDC per NLP, stored in X18 format)
- User submits `MintNlp` with `quoteAmount = 1` (1 raw unit of quote)

**Execution trace in `mintNlp`:**

1. `quoteAmount = int128(1)` — line 465
2. `nlpAmount = MathSD21x18.div(1, 2e18)` = `(1 * 1e18) / 2e18` = `0` — line 466
3. `_validateNlpRebalance(...)` passes because `sum(nlpPoolRebalanceX18) == 1 == quoteAmount` — line 468
4. `spotEngine.updateBalance(NLP_PRODUCT_ID, sender, 0)` — no NLP minted — line 473
5. `spotEngine.updateBalance(QUOTE_PRODUCT_ID, sender, -1)` — quote deducted — line 476
6. `_applyNlpRebalance(...)` — `quoteAmount = 1` distributed to NLP pools — line 477
7. Health check passes (user lost quote, health may have decreased but remains ≥ 0)

**Result:** User loses `quoteAmount = 1` raw quote units. NLP pool holders gain `1` raw quote unit. User receives 0 NLP. Transaction succeeds with no revert. [7](#0-6)

### Citations

**File:** core/contracts/Clearinghouse.sol (L428-436)
```text
        require(
            nlpPools.length == nlpPoolRebalanceX18.length,
            ERR_INVALID_NLP_REBALANCE
        );
        int128 rebalanceAmount = 0;
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            rebalanceAmount += nlpPoolRebalanceX18[i];
        }
        require(deltaQuoteAmount == rebalanceAmount, ERR_INVALID_NLP_REBALANCE);
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

**File:** core/contracts/libraries/MathSD21x18.sol (L62-68)
```text
    function div(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            require(y != 0, ERR_DIV_BY_ZERO);
            int256 result = (int256(x) * ONE_X18) / y;
            require(result >= MIN_X18 && result <= MAX_X18, ERR_OVERFLOW);
            return int128(result);
        }
```

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
