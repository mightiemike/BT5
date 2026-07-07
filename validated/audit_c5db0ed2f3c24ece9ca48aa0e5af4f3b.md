### Title
`SpotEngine::addOrUpdateProduct` Updates Interest Rate Config Without First Settling Pending Interest — (File: `core/contracts/SpotEngine.sol`)

---

### Summary

`SpotEngine.addOrUpdateProduct` overwrites `configs[productId]` with new interest rate parameters without first calling `_updateState` to settle accrued interest using the old parameters. Any interest that has accrued since the last `updateStates` call will be retroactively computed with the new config, corrupting the cumulative deposit and borrow multipliers for all users of that product.

---

### Finding Description

`SpotEngineState._updateState` reads `configs[productId]` to derive the borrow rate curve and minimum deposit rate at the moment it is called: [1](#0-0) 

Specifically, `config.interestFloorX18`, `config.interestSmallCapX18`, `config.interestLargeCapX18`, `config.interestInflectionUtilX18`, and `config.minDepositRateX18` are all consumed at call time to compute `borrowRateMultiplierX18` and `depositRateMultiplierX18`, which are then folded into `state.cumulativeBorrowsMultiplierX18` and `state.cumulativeDepositsMultiplierX18`. [2](#0-1) 

`SpotEngine.addOrUpdateProduct` replaces the config directly without first flushing pending interest: [3](#0-2) 

The assignment `configs[productId] = config` at line 83 takes effect immediately. The next time `_updateState` is invoked (by the sequencer's periodic `updateStates` call), it computes interest for the entire elapsed interval `dt` — which spans both the pre-update and post-update periods — entirely using the new config. The old rate is never applied to the time that elapsed before the config change.

---

### Impact Explanation

**Impact: Medium.**

The `cumulativeDepositsMultiplierX18` and `cumulativeBorrowsMultiplierX18` for the affected product are permanently miscalculated from the moment of the config update onward. Every user balance for that product is derived from these multipliers: [4](#0-3) 

If the new config raises the interest rate, borrowers are overcharged and depositors are over-credited for the pre-update period. If it lowers the rate, the opposite occurs. The error compounds with each subsequent `updateStates` call because the corrupted multipliers are the base for all future multiplications.

---

### Likelihood Explanation

**Likelihood: Medium.**

`addOrUpdateProduct` is a routine admin operation used to tune interest rate curves as market conditions change. Any legitimate config update silently introduces the accounting error. No malicious intent is required — the bug fires on every normal config update.

---

### Recommendation

Call `_updateState` for the product before overwriting `configs[productId]`, using the elapsed time since the last state update. This ensures all interest accrued under the old config is settled before the new parameters take effect:

```solidity
function addOrUpdateProduct(...) public onlyOwner {
    bool isNewProduct = _addOrUpdateProduct(...);

    if (!isNewProduct) {
        // Settle pending interest with the old config before overwriting
        uint128 dt = uint128(block.timestamp) - lastUpdateTime[productId];
        if (dt > 0) {
            State memory state = states[productId];
            _updateState(productId, state, dt);
            _setState(productId, state);
            lastUpdateTime[productId] = uint128(block.timestamp);
        }
    }

    configs[productId] = config;
    ...
}
```

---

### Proof of Concept

1. Product `P` has `interestFloorX18 = 2%` (annualized). At `T=0`, `updateStates` is called; `lastUpdateTime = T0`.
2. At `T=3600` (1 hour later), the owner calls `addOrUpdateProduct` with `interestFloorX18 = 20%`. `configs[P]` is overwritten immediately. No interest is settled.
3. At `T=7200` (another hour later), the sequencer calls `updateStates` with `dt = 7200s` (2 hours).
4. `_updateState` computes the borrow rate using `interestFloorX18 = 20%` for the full 2-hour window.
5. The first hour of interest — which should have been computed at 2% — is instead computed at 20%, inflating `cumulativeBorrowsMultiplierX18` and `cumulativeDepositsMultiplierX18` by approximately `(20% - 2%) / 2 ≈ 9%` of one hour's worth of interest, permanently corrupting all user balances for product `P`. [5](#0-4) [3](#0-2)

### Citations

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

**File:** core/contracts/SpotEngineState.sol (L129-137)
```text
        state.cumulativeBorrowsMultiplierX18 = state
            .cumulativeBorrowsMultiplierX18
            .mul(borrowRateMultiplierX18);

        int128 depositRateMultiplierX18 = ONE + realizedDepositRateX18;

        state.cumulativeDepositsMultiplierX18 = state
            .cumulativeDepositsMultiplierX18
            .mul(depositRateMultiplierX18);
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
