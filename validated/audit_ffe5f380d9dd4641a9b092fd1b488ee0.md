### Title
Stale Utilization Rate in `SpotTick` Interest Accrual Due to Missing `_updateState` Call on Deposit/Withdrawal — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

`SpotEngine._updateState()` computes interest accrual using the *current* `utilizationRatioX18` at the moment a `SpotTick` is processed. Because `depositCollateral` and `withdrawCollateral` modify `totalDepositsNormalized` without first snapshotting the cumulative multipliers, the next `SpotTick` retroactively applies the post-deposit/withdrawal utilization rate to the entire elapsed period `dt`. This causes systematic under- or over-charging of borrowing interest, directly corrupting `cumulativeDepositsMultiplierX18` and `cumulativeBorrowsMultiplierX18` for all subaccounts in the affected product.

---

### Finding Description

**State variables at risk:**
`SpotEngine.State.cumulativeDepositsMultiplierX18` and `cumulativeDepositsMultiplierX18` (stored in `states[productId]`) are the Nado analogs of RFX's `CUMULATIVE_BORROWING_FACTOR`. They are updated exclusively inside `_updateState()`, which is called only via `spotEngine.updateStates(dt)` triggered by a `SpotTick` transaction. [1](#0-0) 

`_updateState()` computes the borrow rate from the utilization ratio at the moment it is called:

```
utilizationRatioX18 = totalBorrows / totalDeposits   // snapshot at SpotTick time
borrowRateMultiplier = (1 + borrowerRatePerSecond)^dt
``` [2](#0-1) 

This multiplier is then applied to the *entire* elapsed period `dt` since the last `SpotTick`.

**The missing update in `depositCollateral`:**

`Clearinghouse.depositCollateral` calls `spotEngine.updateBalance()` directly, which calls `_updateBalanceNormalized()` and modifies `state.totalDepositsNormalized` — without first calling `_updateState()` to snapshot the cumulative multipliers at the pre-deposit utilization rate. [3](#0-2) 

**The missing update in `withdrawCollateral`:**

`Clearinghouse.withdrawCollateral` has the same omission — it calls `spotEngine.updateBalance()` to reduce `totalDepositsNormalized` without first snapshotting the cumulative multipliers. [4](#0-3) 

**`updateStates` is only called from `SpotTick`:** [5](#0-4) 

There is no other call site for `spotEngine.updateStates()`. Deposits and withdrawals never trigger it.

---

### Impact Explanation

**Accounting corruption of `cumulativeDepositsMultiplierX18` / `cumulativeBorrowsMultiplierX18`:**

- **Large deposit before `SpotTick`**: `totalDepositsNormalized` increases → utilization drops → next `SpotTick` applies a lower borrow rate for the entire `dt` period. Borrowers underpay interest for the time before the deposit. Depositors receive less yield than owed.
- **Large withdrawal before `SpotTick`**: `totalDepositsNormalized` decreases → utilization spikes → next `SpotTick` applies a higher borrow rate for the entire `dt` period. Borrowers overpay interest for the time before the withdrawal.

Because `cumulativeBorrowsMultiplierX18` is used to denormalize all borrow balances across every subaccount in the product, a single deposit/withdrawal event corrupts the interest owed by *all* borrowers in that product for the entire `dt` window. The magnitude scales with deposit size, elapsed time `dt`, and the slope of the interest rate curve (`interestLargeCapX18`). [6](#0-5) 

---

### Likelihood Explanation

**High.** Any user with a spot balance can call `depositCollateral` or `withdrawCollateral` through the Endpoint's slow-mode path or via a sequencer-submitted transaction. The `SpotTick` interval is a known, observable on-chain event. A borrower can:

1. Observe the time since the last `SpotTick` (large `dt` → large impact).
2. Deposit a large amount of collateral immediately before the next `SpotTick` is submitted.
3. After the `SpotTick` processes with artificially low utilization (and thus lower borrow rate applied retroactively), withdraw the deposit.

This requires no privileged access, no leaked keys, and no governance capture. The slow-mode path is permissionless for `DepositCollateral`. [7](#0-6) 

---

### Recommendation

Before modifying `totalDepositsNormalized` or `totalBorrowsNormalized` in any deposit or withdrawal path, call `_updateState()` to snapshot the cumulative multipliers at the current utilization rate. Concretely, in `SpotEngine.updateBalance()` (both overloads), call `_updateState(productId, state, dt)` with the elapsed time before calling `_updateBalanceNormalized()`. The elapsed time `dt` should be derived from the last recorded `spotTime` stored in the Endpoint's `times` struct, analogous to how `SpotTick` computes it. [8](#0-7) 

---

### Proof of Concept

**Setup:** Product with `interestFloorX18 = 1e16`, `interestLargeCapX18 = ONE`, `interestInflectionUtilX18 = 8e17`. Total deposits = 1000 USDC, total borrows = 800 USDC (80% utilization → high borrow rate). Last `SpotTick` was 1 hour ago (`dt = 3600s`).

**Attack:**
1. Attacker (a borrower) deposits 9000 USDC → utilization drops from 80% to 8%.
2. Sequencer submits `SpotTick` with `dt = 3600`.
3. `_updateState()` computes `utilizationRatioX18 = 800/10000 = 8%` and applies the near-floor borrow rate for the entire 3600 seconds.
4. Attacker withdraws 9000 USDC.

**Result:** The attacker's borrow balance accrues ~1 hour of interest at ~8% utilization rate instead of ~80% utilization rate. The difference in annualized borrow rate between 8% and 80% utilization is approximately `interestSmallCapX18 * (0.8 - 0.08) / 0.8 + interestLargeCapX18 * 0` vs the full rate — a reduction of roughly 3.6% annualized. Over 1 hour on 800 USDC of borrows, this is a measurable underpayment of interest, and the effect compounds across every borrower in the product for that tick window. [9](#0-8)

### Citations

**File:** core/contracts/SpotEngineState.sol (L52-98)
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

**File:** core/contracts/Clearinghouse.sol (L193-209)
```text
    function depositCollateral(IEndpoint.DepositCollateral calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
    }
```

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```

**File:** core/contracts/EndpointTx.sol (L209-216)
```text
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            IEndpoint.DepositCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositCollateral)
            );
            validateSender(txn.sender, sender);
            _recordSubaccount(txn.sender);
            clearinghouse.depositCollateral(txn);
```

**File:** core/contracts/EndpointTx.sol (L466-475)
```text
        } else if (txType == IEndpoint.TransactionType.SpotTick) {
            IEndpoint.SpotTick memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.SpotTick)
            );
            Times memory t = times;
            uint128 dt = t.spotTime == 0 ? 0 : txn.time - t.spotTime;
            spotEngine.updateStates(dt);
            t.spotTime = txn.time;
            times = t;
```

**File:** core/contracts/SpotEngine.sol (L176-225)
```text
    function updateBalance(
        uint32 productId,
        bytes32 subaccount,
        int128 amountDelta,
        int128 quoteDelta
    ) external {
        require(productId != QUOTE_PRODUCT_ID, ERR_INVALID_PRODUCT);
        _assertInternal();
        State memory state = states[productId];
        State memory quoteState = states[QUOTE_PRODUCT_ID];

        BalanceNormalized memory balance = balances[productId][subaccount];

        BalanceNormalized memory quoteBalance = balances[QUOTE_PRODUCT_ID][
            subaccount
        ];

        if (productId == NLP_PRODUCT_ID) {
            handleNlpLockedBalance(subaccount, amountDelta);
        }

        _updateBalanceNormalized(state, balance, amountDelta);
        _updateBalanceNormalized(quoteState, quoteBalance, quoteDelta);

        _setBalanceAndUpdateBitmap(productId, subaccount, balance);
        _setBalanceAndUpdateBitmap(QUOTE_PRODUCT_ID, subaccount, quoteBalance);

        _setState(productId, state);
        _setState(QUOTE_PRODUCT_ID, quoteState);
    }

    function updateBalance(
        uint32 productId,
        bytes32 subaccount,
        int128 amountDelta
    ) external {
        _assertInternal();

        State memory state = states[productId];

        if (productId == NLP_PRODUCT_ID) {
            handleNlpLockedBalance(subaccount, amountDelta);
        }

        BalanceNormalized memory balance = balances[productId][subaccount];
        _updateBalanceNormalized(state, balance, amountDelta);

        _setBalanceAndUpdateBitmap(productId, subaccount, balance);
        _setState(productId, state);
    }
```
