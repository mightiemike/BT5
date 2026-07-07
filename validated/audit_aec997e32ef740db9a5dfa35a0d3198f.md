### Title
Loss of Accrued Interest When Updating Spot Product Interest Rate Config - (File: `core/contracts/SpotEngine.sol`)

---

### Summary
`SpotEngine.addOrUpdateProduct()` overwrites the interest rate `Config` for an existing product without first calling `_updateState()` to settle pending interest under the old parameters. The next periodic `updateStates()` call will compute interest using the new config for the entire elapsed time interval, permanently corrupting `cumulativeBorrowsMultiplierX18` and `cumulativeDepositsMultiplierX18` and causing a loss or windfall of accrued interest for all depositors and borrowers of that product.

---

### Finding Description

`SpotEngine.addOrUpdateProduct()` handles both new product creation and updates to existing products: [1](#0-0) 

For an existing product (`isNewProduct == false`), the function directly overwrites `configs[productId] = config` at line 83 with no prior interest settlement. The `Config` struct contains all interest rate model parameters: `interestFloorX18`, `interestSmallCapX18`, `interestLargeCapX18`, `interestInflectionUtilX18`, and `minDepositRateX18`. [2](#0-1) 

Interest accrual happens in `SpotEngineState._updateState()`, which reads `configs[productId]` to compute the borrow rate multiplier for the elapsed time `dt`: [3](#0-2) 

This function is only called from `updateStates()`, which is invoked periodically by the Endpoint with an off-chain-supplied `dt`: [4](#0-3) 

Because `_updateState()` requires an externally supplied `dt` and no last-update timestamp is stored in the `State` struct, there is no on-chain mechanism to settle pending interest before the config is replaced. The next `updateStates()` call will apply the new interest rate parameters retroactively over the entire interval since the last update, as if the new config had been in effect the whole time.

---

### Impact Explanation

When the owner updates the interest rate config for an existing spot product:

- If the new rate is **lower** than the old rate: the pending interest that should have accrued at the old (higher) rate is permanently lost. `cumulativeBorrowsMultiplierX18` and `cumulativeDepositsMultiplierX18` will be smaller than they should be. Depositors receive less interest than they earned; the deficit is unrecoverable.
- If the new rate is **higher** than the old rate: borrowers are retroactively charged more than they should have been for the period before the config change.

The corrupted multipliers affect every user's balance for that product, since `balanceNormalizedToBalance()` multiplies `amountNormalized` by the cumulative multiplier: [5](#0-4) 

The magnitude of the impact scales with: (1) the size of the rate change, (2) the total deposits/borrows in the product, and (3) the time elapsed since the last `updateStates()` call.

---

### Likelihood Explanation

Medium. The owner is expected to update product configs as part of normal protocol operations (e.g., adjusting interest rate curves in response to market conditions). There is no warning in the code or documentation that `updateStates()` must be called first. Any routine config update to an existing spot product triggers this issue. The Endpoint's `updateStates()` is called periodically off-chain, so there will always be a non-zero `dt` window during which the config change takes effect retroactively.

---

### Recommendation

Before overwriting `configs[productId]` for an existing product, the protocol must settle pending interest under the old config. Two approaches:

1. **Store a last-update timestamp** in the `State` struct so that `_updateState()` can be called on-demand with a computed `dt` directly inside `addOrUpdateProduct()`.
2. **Require a prior `updateStates()` call** by checking that the state was updated in the same block, or by exposing a permissioned `accrueInterest(uint32 productId, uint128 dt)` function that must be called before any config update.

---

### Proof of Concept

1. Product `X` has `interestFloorX18 = 1e16` (1% floor). `updateStates()` was last called at time `T0`.
2. At time `T1 = T0 + 86400` (24 hours later), the owner calls `addOrUpdateProduct(X, ..., newConfig)` where `newConfig.interestFloorX18 = 0` (0% floor). `configs[X]` is overwritten immediately.
3. At time `T2 = T1 + 1`, the Endpoint calls `updateStates(dt = T2 - T0 = 86401)`.
4. `_updateState()` reads the new config with `interestFloorX18 = 0` and computes the borrow rate for the full 86401-second interval using the new (lower) rate.
5. The 24 hours of interest that should have accrued at 1% floor is permanently lost from `cumulativeBorrowsMultiplierX18` and `cumulativeDepositsMultiplierX18`, reducing depositor balances across the entire product. [6](#0-5)

### Citations

**File:** core/contracts/SpotEngine.sol (L68-97)
```text
    function addOrUpdateProduct(
        uint32 productId,
        uint32 quoteId,
        int128 sizeIncrement,
        int128 minSize,
        Config calldata config,
        RiskHelper.RiskStore calldata riskStore
    ) public onlyOwner {
        bool isNewProduct = _addOrUpdateProduct(
            productId,
            quoteId,
            sizeIncrement,
            minSize,
            riskStore
        );
        configs[productId] = config;

        if (isNewProduct) {
            require(productId != QUOTE_PRODUCT_ID);
            _setState(
                productId,
                State({
                    cumulativeDepositsMultiplierX18: ONE,
                    cumulativeBorrowsMultiplierX18: ONE,
                    totalDepositsNormalized: 0,
                    totalBorrowsNormalized: 0
                })
            );
        }
    }
```

**File:** core/contracts/SpotEngineState.sol (L52-100)
```text
    function _updateState(
        uint32 productId,
        State memory state,
        uint128 dt
    ) internal {
        int128 borrowRateMultiplierX18;
        int128 totalDeposits = state.totalDepositsNormalized.mul(
            state.cumulativeDepositsMultiplierX18
        );
        int128 totalBorrows = state.totalBorrowsNormalized.mul(
            state.cumulativeBorrowsMultiplierX18
        );
        int128 utilizationRatioX18 = totalBorrows.div(totalDeposits);
        int128 minDepositRateX18;
        {
            Config memory config = configs[productId];

            // annualized borrower rate
            int128 borrowerRateX18 = config.interestFloorX18;
            if (utilizationRatioX18 == 0) {
                // setting borrowerRateX18 to 0 here has the property that
                // adding a product at the beginning of time and not using it until time T
                // results in the same state as adding the product at time T
                borrowerRateX18 = 0;
            } else if (utilizationRatioX18 < config.interestInflectionUtilX18) {
                borrowerRateX18 += config
                    .interestSmallCapX18
                    .mul(utilizationRatioX18)
                    .div(config.interestInflectionUtilX18);
            } else {
                borrowerRateX18 +=
                    config.interestSmallCapX18 +
                    config.interestLargeCapX18.mul(
                        (
                            (utilizationRatioX18 -
                                config.interestInflectionUtilX18).div(
                                    ONE - config.interestInflectionUtilX18
                                )
                        )
                    );
            }

            // convert to per second
            borrowerRateX18 = borrowerRateX18.div(
                MathSD21x18.fromInt(31536000)
            );
            borrowRateMultiplierX18 = (ONE + borrowerRateX18).pow(int128(dt));
            minDepositRateX18 = config.minDepositRateX18;
        }
```

**File:** core/contracts/SpotEngineState.sol (L180-192)
```text
    function balanceNormalizedToBalance(
        State memory state,
        BalanceNormalized memory balance
    ) internal pure returns (Balance memory) {
        int128 cumulativeMultiplierX18;
        if (balance.amountNormalized > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        return Balance(balance.amountNormalized.mul(cumulativeMultiplierX18));
    }
```

**File:** core/contracts/SpotEngineState.sol (L265-283)
```text
    function updateStates(uint128 dt) external onlyEndpoint {
        State memory quoteState;
        require(dt < 7 * SECONDS_PER_DAY, ERR_INVALID_TIME);
        for (uint32 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            if (productId == NLP_PRODUCT_ID) {
                continue;
            }
            State memory state = states[productId];
            if (productId == QUOTE_PRODUCT_ID) {
                quoteState = state;
            }
            if (state.totalDepositsNormalized == 0) {
                continue;
            }
            _updateState(productId, state, dt);
            _setState(productId, state);
        }
    }
```
