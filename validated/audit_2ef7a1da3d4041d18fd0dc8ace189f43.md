### Title
Interest Rate Config Update Does Not Flush Accrued State, Causing Retroactive Rate Misapplication — (File: `core/contracts/SpotEngine.sol`)

---

### Summary

`SpotEngine.addOrUpdateProduct()` overwrites `configs[productId]` with new interest rate parameters without first settling accrued interest at the old rate. The next sequencer-driven `updateStates()` call then applies the new rate to the **entire elapsed period** `dt`, including the time when the old config was in effect, causing borrowers to be overcharged or lenders to be underpaid.

---

### Finding Description

`SpotEngine.addOrUpdateProduct()` is the admin function used to update a product's interest rate configuration (`interestFloorX18`, `interestSmallCapX18`, `interestLargeCapX18`, `interestInflectionUtilX18`, `minDepositRateX18`). It directly overwrites `configs[productId]` with no prior state flush: [1](#0-0) 

Interest accrual is performed lazily in `SpotEngineState.updateStates()`, which is called periodically by the sequencer via the Endpoint. It passes a `dt` value representing the full elapsed time since the last call: [2](#0-1) 

Inside `_updateState()`, the config is read fresh at the time of the call: [3](#0-2) 

The borrow rate multiplier is computed over the full `dt` window using the **current** config. There is no mechanism to split `dt` at the moment the config changed.

**Desynchronization sequence:**

1. At time `T0`: `updateStates(dt0)` is called. State is settled with old config `C_old`.
2. At time `T1` (`T1 > T0`): admin calls `addOrUpdateProduct(productId, ..., C_new, ...)`. `configs[productId]` is overwritten. No `_updateState` is called.
3. At time `T2` (`T2 > T1`): `updateStates(T2 - T0)` is called. `_updateState` reads `C_new` and applies it to the **entire** `dt = T2 - T0` window, including the `T1 - T0` sub-period when `C_old` was the correct rate.

The cumulative multipliers are then permanently set to incorrect values: [4](#0-3) 

---

### Impact Explanation

- **If `C_new` has a higher borrow rate than `C_old`**: borrowers are charged the elevated rate for the entire `T2 - T0` window, including the `T1 - T0` sub-period when the lower rate was contractually in effect. This is an unearned extraction from borrowers.
- **If `C_new` has a lower borrow rate than `C_old`**: lenders/depositors receive a reduced deposit multiplier for the entire window, losing interest they were entitled to for the `T1 - T0` sub-period.

The corruption is permanent: `cumulativeDepositsMultiplierX18` and `cumulativeBorrowsMultiplierX18` are written back to storage with the incorrect values, and all subsequent normalized-to-actual balance conversions use these corrupted multipliers. [5](#0-4) 

---

### Likelihood Explanation

The sequencer calls `updateStates` on a regular cadence (bounded by `dt < 7 * SECONDS_PER_DAY`). Any config update that occurs between two sequencer ticks — a routine operational event — triggers the desynchronization. The magnitude of the impact scales with the time elapsed since the last `updateStates` call and the magnitude of the rate change. This is a realistic operational scenario with no special preconditions beyond a legitimate admin config update.

---

### Recommendation

Before overwriting `configs[productId]` in `addOrUpdateProduct()`, call `_updateState()` for that product to settle all accrued interest at the old rate. This ensures the new rate is only applied to time elapsed **after** the config change:

```solidity
function addOrUpdateProduct(..., Config calldata config, ...) public onlyOwner {
    bool isNewProduct = _addOrUpdateProduct(...);

    // Flush accrued interest at old rate before changing config
    if (!isNewProduct && states[productId].totalDepositsNormalized != 0) {
        State memory state = states[productId];
        uint128 dt = /* elapsed time since last update */;
        _updateState(productId, state, dt);
        _setState(productId, state);
    }

    configs[productId] = config;
    ...
}
```

Additionally, implement off-chain monitoring to alert when a config update occurs and verify that the next `updateStates` call correctly reflects the split-period accounting.

---

### Proof of Concept

1. Product `P` has `interestFloorX18 = 1e16` (1% APR). At `T0`, `updateStates(dt0)` is called; state is settled.
2. At `T1 = T0 + 12 hours`, admin calls `addOrUpdateProduct(P, ..., Config{interestFloorX18: 1e17, ...}, ...)` — raising the floor to 10% APR. No state flush occurs.
3. At `T2 = T0 + 24 hours`, the sequencer calls `updateStates(86400)` (`dt = 24 hours`).
4. `_updateState` reads the new config (`10% APR`) and computes `borrowRateMultiplierX18` for the full 24-hour window.
5. Borrowers are charged 10% APR for 24 hours instead of 1% APR for 12 hours + 10% APR for 12 hours — a ~4.5× overcharge for the first half of the window.
6. `cumulativeBorrowsMultiplierX18` is permanently inflated; all future balance reads via `balanceNormalizedToBalance` return inflated borrow amounts. [6](#0-5) [7](#0-6)

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
