### Title
Interest Rate Config Update Does Not Sync Accrued State Before Applying New Parameters — (File: `core/contracts/SpotEngine.sol`)

---

### Summary

`SpotEngine.addOrUpdateProduct` overwrites the interest rate configuration (`interestFloorX18`, `interestSmallCapX18`, `interestLargeCapX18`, `interestInflectionUtilX18`, `minDepositRateX18`) without first calling `updateStates` to accrue interest up to the current block with the old parameters. The next sequencer-driven `updateStates` call will apply the new rate retroactively across the entire elapsed interval, corrupting `cumulativeDepositsMultiplierX18` and `cumulativeBorrowsMultiplierX18` for all depositors and borrowers of that product.

---

### Finding Description

In `SpotEngine.sol`, `addOrUpdateProduct` (lines 68–97) directly writes the new config at line 83:

```solidity
configs[productId] = config;
``` [1](#0-0) 

No call to `updateStates` precedes this assignment. The `_updateState` function in `SpotEngineState.sol` (lines 52–178) reads `configs[productId]` to derive the per-second borrow rate and deposit rate multipliers for the elapsed time `dt`:

```solidity
Config memory config = configs[productId];
int128 borrowerRateX18 = config.interestFloorX18;
...
borrowRateMultiplierX18 = (ONE + borrowerRateX18).pow(int128(dt));
``` [2](#0-1) 

These multipliers are then compounded into the global accumulators:

```solidity
state.cumulativeBorrowsMultiplierX18 = state.cumulativeBorrowsMultiplierX18.mul(borrowRateMultiplierX18);
state.cumulativeDepositsMultiplierX18 = state.cumulativeDepositsMultiplierX18.mul(depositRateMultiplierX18);
``` [3](#0-2) 

Because `updateStates` is called by the sequencer with a `dt` covering the full interval since the last call, any config change that occurs mid-interval causes the new rate to be applied to the **entire** interval — including the portion that elapsed under the old rate. This is the same root cause as the reported `AutopoolFees` bug: a rate parameter is mutated without first settling accumulated state at the old rate.

---

### Impact Explanation

All normalized balances (`amountNormalized`) for depositors and borrowers of the affected spot product are denominated in units of `cumulativeDepositsMultiplierX18` / `cumulativeBorrowsMultiplierX18`. A retroactive jump in these accumulators causes every user's real balance (computed as `amountNormalized × cumulativeMultiplier`) to be misstated. Concretely:

- If the new rate is **higher** than the old rate, borrowers are overcharged and depositors are over-credited for the pre-change interval.
- If the new rate is **lower**, borrowers are undercharged and depositors are under-credited.

The corrupted multipliers persist permanently and affect every subsequent balance read, health check, and liquidation calculation for that product. [4](#0-3) 

---

### Likelihood Explanation

`addOrUpdateProduct` is a routine admin operation used to adjust interest rate parameters for any spot product (including the quote asset). The sequencer calls `updateStates` on a regular cadence. Any config update that lands between two sequencer ticks — which is the normal case — triggers the retroactive application. No special timing or adversarial setup is required beyond the admin making a legitimate configuration change. [5](#0-4) 

---

### Recommendation

Before overwriting `configs[productId]`, call the internal `_updateState` for that product with the elapsed time since the last state update (or expose a pre-settlement step). This ensures all interest accrued under the old parameters is committed to `cumulativeBorrowsMultiplierX18` and `cumulativeDepositsMultiplierX18` before the new rate takes effect — mirroring the fix recommended for `AutopoolFees` (call `updateDebtReporting` before changing the fee).

---

### Proof of Concept

1. At time `T0`, the sequencer calls `updateStates` for product `P`; `cumulativeBorrowsMultiplierX18` is settled.
2. At time `T1 = T0 + 3600s`, the admin calls `addOrUpdateProduct` for product `P`, raising `interestFloorX18` from 1% to 10% annualized. `configs[P]` is overwritten immediately; no interest is accrued for the `[T0, T1]` interval.
3. At time `T2 = T1 + 3600s`, the sequencer calls `updateStates` with `dt = 7200s` (covering `[T0, T2]`).
4. `_updateState` uses the **new** `interestFloorX18 = 10%` for the full 7200-second interval, charging borrowers 10% for the entire window instead of 1% for `[T0, T1]` and 10% for `[T1, T2]`.
5. `cumulativeBorrowsMultiplierX18` is permanently inflated; all borrower balances are overstated in debt, and all depositor balances are over-credited — a permanent accounting corruption for the product. [6](#0-5)

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

**File:** core/contracts/SpotEngineState.sol (L31-50)
```text
        }

        int128 newAmount = balance.amountNormalized.mul(
            cumulativeMultiplierX18
        ) + balanceDelta;

        if (newAmount > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        balance.amountNormalized = newAmount.div(cumulativeMultiplierX18);

        if (balance.amountNormalized > 0) {
            state.totalDepositsNormalized += balance.amountNormalized;
        } else {
            state.totalBorrowsNormalized -= balance.amountNormalized;
        }
    }
```

**File:** core/contracts/SpotEngineState.sol (L66-99)
```text
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
```

**File:** core/contracts/SpotEngineState.sol (L103-144)
```text
        // (deposits - borrows) should remain the same after updating state.

        // For simplicity, we use `tb`, `cbm`, `td`, and `cdm` for
        // `totalBorrowsNormalized`, `cumulativeBorrowsMultiplier`,
        // `totalDepositsNormalized`, and `cumulativeDepositsMultiplier`

        // before the updating, the liquidity is (td * cdm - tb * cbm)
        // after the updating, the liquidity is
        // (td * cdm * depositRateMultiplier - tb * cbm * borrowRateMultiplier)
        // so we can get
        // depositRateMultiplier = utilization * (borrowRateMultiplier - 1) + 1

        int128 totalDepositRateX18 = utilizationRatioX18.mul(
            borrowRateMultiplierX18 - ONE
        );

        // deduct protocol fees
        int128 realizedDepositRateX18 = totalDepositRateX18.mul(
            ONE - INTEREST_FEE_FRACTION
        );

        // pass fees balance change
        int128 feesAmt = totalDeposits.mul(
            totalDepositRateX18 - realizedDepositRateX18
        );

        state.cumulativeBorrowsMultiplierX18 = state
            .cumulativeBorrowsMultiplierX18
            .mul(borrowRateMultiplierX18);

        int128 depositRateMultiplierX18 = ONE + realizedDepositRateX18;

        state.cumulativeDepositsMultiplierX18 = state
            .cumulativeDepositsMultiplierX18
            .mul(depositRateMultiplierX18);

        if (feesAmt != 0) {
            BalanceNormalized memory feesAccBalance = balances[productId][
                FEES_ACCOUNT
            ];
            _updateBalanceNormalized(state, feesAccBalance, feesAmt);
            _setBalanceAndUpdateBitmap(productId, FEES_ACCOUNT, feesAccBalance);
```
