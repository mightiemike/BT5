### Title
Interest Rate Not Checkpointed Before Pool Balance Changes in `mintNlp` and `depositCollateral` — (`Clearinghouse.sol`)

---

### Summary

When users mint NLP tokens or deposit collateral, the `SpotEngine`'s cumulative interest multipliers are not updated (checkpointed) before the pool's `totalDepositsNormalized` is increased. The next call to `updateStates` will apply the interest for the entire elapsed period using the post-deposit (lower) utilization ratio, causing borrowers to systematically underpay interest and depositors to earn less than they are owed.

---

### Finding Description

In `SpotEngineState._updateState`, the borrow rate for a time period `dt` is computed directly from the current `totalDepositsNormalized` and `totalBorrowsNormalized`:

```solidity
// SpotEngineState.sol lines 58–64
int128 totalDeposits = state.totalDepositsNormalized.mul(
    state.cumulativeDepositsMultiplierX18
);
int128 totalBorrows = state.totalBorrowsNormalized.mul(
    state.cumulativeBorrowsMultiplierX18
);
int128 utilizationRatioX18 = totalBorrows.div(totalDeposits);
``` [1](#0-0) 

This utilization ratio is then used to compound `cumulativeBorrowsMultiplierX18` and `cumulativeDepositsMultiplierX18` for the full `dt` window. [2](#0-1) 

The invariant that must hold is: **`updateStates(dt)` must be called with the pre-change utilization before any operation that alters `totalDepositsNormalized` or `totalBorrowsNormalized`.**

`Clearinghouse.mintNlp` violates this invariant. It calls `spotEngine.updateBalance` on both the NLP product and the QUOTE product (via `_applyNlpRebalance`) without first calling `spotEngine.updateStates`:

```solidity
// Clearinghouse.sol lines 473–477
spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
_applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
``` [3](#0-2) 

`_applyNlpRebalance` increases the QUOTE balance of NLP pool subaccounts, directly raising `totalDepositsNormalized` for the QUOTE product: [4](#0-3) 

`SpotEngine.updateBalance` (the single-argument overload used for QUOTE) calls `_updateBalanceNormalized` which mutates `state.totalDepositsNormalized` and then persists it via `_setState` — with no call to `_updateState`: [5](#0-4) 

The same pattern applies to `Clearinghouse.depositCollateral`, which calls `spotEngine.updateBalance` directly: [6](#0-5) 

---

### Impact Explanation

Let `T0` = last time `updateStates` was called, `T1` = time of the NLP mint, `T2` = next time `updateStates(dt)` is called with `dt = T2 − T0`.

The entire `dt` window is compounded at the **post-mint** utilization ratio (lower, because `totalDepositsNormalized` increased at `T1`). The correct behavior is to compound `T0→T1` at the pre-mint utilization and `T1→T2` at the post-mint utilization.

Consequence: borrowers underpay interest for the `T0→T1` window; depositors earn less than owed. Because health calculations in `getHealth` depend on the accrued interest embedded in `cumulativeBorrowsMultiplierX18`, this also causes undercollateralized positions to appear healthier than they are, delaying or preventing legitimate liquidations. [7](#0-6) 

---

### Likelihood Explanation

The slow-mode execution path (`executeSlowModeTransaction`) is callable by any user after a 3-day timeout and processes the deposit without any surrounding `updateStates` call: [8](#0-7) 

Even in the sequencer-batched path (`submitTransactionsChecked`), there is no on-chain enforcement that `UpdateSpotStates` precedes `DepositCollateral` or `MintNlp` within the same batch. Any batch ordering that places a balance-increasing operation before the state update triggers the bug. This is reachable by any unprivileged user who deposits collateral or mints NLP.

---

### Recommendation

Before any call to `spotEngine.updateBalance` that increases `totalDepositsNormalized`, call `spotEngine.updateStates(dt)` with the elapsed time since the last update. The contract should track the last update timestamp on-chain so that `dt` can be computed trustlessly, rather than relying on the sequencer to supply it correctly and in the right order.

---

### Proof of Concept

1. At time `T0`, `updateStates(dt0)` is called. Utilization = 80% (high borrow rate).
2. At time `T1 = T0 + 1 day`, a user calls `mintNlp` with a large `quoteAmount`, doubling `totalDepositsNormalized`. Utilization drops to 40%.
3. At time `T2 = T0 + 2 days`, `updateStates(dt = 2 days)` is called.
4. **Actual**: interest for 2 full days is compounded at 40% utilization.
5. **Correct**: interest for day 1 at 80% utilization + interest for day 2 at 40% utilization.
6. Borrowers pay ~half the interest they owe for day 1. Depositors are shortchanged. Unhealthy positions that should have been liquidatable after day 1 remain above the maintenance threshold. [9](#0-8) [10](#0-9)

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

**File:** core/contracts/Clearinghouse.sol (L207-208)
```text
        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
```

**File:** core/contracts/Clearinghouse.sol (L439-451)
```text
    function _applyNlpRebalance(
        ISpotEngine spotEngine,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) internal {
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                nlpPools[i].subaccount,
                nlpPoolRebalanceX18[i]
            );
        }
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

**File:** core/contracts/SpotEngine.sol (L207-225)
```text
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

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```
