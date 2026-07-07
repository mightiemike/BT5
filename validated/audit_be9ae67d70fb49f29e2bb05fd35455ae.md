### Title
Stale Spot Borrow Multiplier Used in Health Checks Allows Collateral Withdrawal Into Bad Debt — (`core/contracts/SpotEngineState.sol`)

---

### Summary

`SpotEngineState.getBalance()` computes a borrower's liability using the stored `cumulativeBorrowsMultiplierX18` from `states[productId]`, which is only updated when the sequencer calls `SpotEngine.updateStates()`. If `updateStates` has not been called recently — most critically during sequencer downtime when users resort to slow-mode transactions — the returned borrow balance is understated. Because `Clearinghouse.withdrawCollateral()` and `Clearinghouse.getHealth()` rely on this stale balance for their health check, a borrower can withdraw collateral that would leave them insolvent at the true (interest-accrued) liability level, creating bad debt that is socialized across depositors.

---

### Finding Description

`SpotEngineState` stores normalized balances and a pair of cumulative multipliers per product:

```
cumulativeDepositsMultiplierX18
cumulativeBorrowsMultiplierX18
```

These multipliers grow over time as interest accrues. They are advanced only inside `_updateState()`, which is invoked exclusively by `SpotEngine.updateStates(uint128 dt)`. [1](#0-0) 

`updateStates` carries the `onlyEndpoint` modifier, meaning it can only be called by the sequencer submitting a batch: [2](#0-1) 

When `getBalance()` is called it reads `states[productId]` directly from storage — the snapshot at the last `updateStates` call — and multiplies the stored normalized amount by the stored (potentially stale) multiplier: [3](#0-2) [4](#0-3) 

For a borrower (`amountNormalized < 0`), the actual liability is:

```
liability = amountNormalized * cumulativeBorrowsMultiplierX18
```

If `updateStates` has not been called since time `T`, the stored multiplier is smaller than the true current multiplier, so the liability is **understated**.

This stale balance propagates directly into the health check path:

`Clearinghouse.getHealth()` → `BaseEngine.getHealthContribution()` → `_calculateProductHealth()` → `_getBalance()` → `SpotEngineState.getBalance()` [5](#0-4) [6](#0-5) 

`withdrawCollateral` enforces health using this stale value: [7](#0-6) 

---

### Impact Explanation

A borrower can withdraw collateral that makes them insolvent at the true (interest-accrued) liability level. The resulting bad debt is absorbed by the insurance fund and, if exhausted, socialized across all depositors via `SpotEngine.socializeSubaccount()`, permanently reducing the `cumulativeDepositsMultiplierX18` and diluting every depositor's balance. [8](#0-7) 

---

### Likelihood Explanation

The primary trigger is the slow-mode escape hatch. The Endpoint's `slowModeTxs` queue allows users to force a `WithdrawCollateral` transaction when the sequencer is unresponsive. During sequencer downtime, `updateStates` is never called, so the borrow multiplier grows increasingly stale. A user who borrowed at maximum leverage before the outage can submit a slow-mode withdrawal whose health check passes on the stale (understated) liability, but fails on the true current liability. The longer the outage, the larger the exploitable gap. Even during normal operation, any batch that processes a withdrawal without first calling `updateStates` in the same batch creates a small window of staleness.

---

### Recommendation

Before computing health in `withdrawCollateral` (and in `liquidateSubaccountImpl`), apply pending interest to the spot state in memory without writing it to storage — analogous to using `borrowBalance` (which calls `_previewInterest`) instead of `borrowBalanceStored` in Aloe. Concretely, `getBalance()` and `getStateAndBalance()` in `SpotEngineState` should accept an optional elapsed-time parameter and call `_updateState` on the in-memory `State` before computing the denormalized balance, so that health checks always reflect the true current liability. [9](#0-8) [10](#0-9) 

---

### Proof of Concept

1. User borrows the maximum allowed spot asset amount (e.g., USDC) at near-maximum leverage, leaving a small safety margin so the position is initially healthy.
2. The sequencer goes offline (or the user waits for a period with no `updateStates` calls).
3. Interest accrues: the true `cumulativeBorrowsMultiplierX18` grows, but `states[productId].cumulativeBorrowsMultiplierX18` remains at its last stored value.
4. User submits a slow-mode `WithdrawCollateral` transaction for the maximum amount that passes the health check using the stale multiplier.
5. `Clearinghouse.withdrawCollateral` calls `getHealth`, which calls `SpotEngineState.getBalance` with the stale multiplier — the liability appears smaller than it truly is, so the health check passes.
6. Collateral is transferred out. When the sequencer resumes and calls `updateStates`, the true multiplier is applied and the account is revealed to be insolvent (bad debt).
7. `socializeSubaccount` is called during liquidation finalization, permanently reducing `cumulativeDepositsMultiplierX18` and diluting all depositors. [11](#0-10) [12](#0-11)

### Citations

**File:** core/contracts/SpotEngineState.sol (L15-50)
```text
    function _updateBalanceNormalized(
        State memory state,
        BalanceNormalized memory balance,
        int128 balanceDelta
    ) internal pure {
        if (balance.amountNormalized > 0) {
            state.totalDepositsNormalized -= balance.amountNormalized;
        } else {
            state.totalBorrowsNormalized += balance.amountNormalized;
        }

        int128 cumulativeMultiplierX18;
        if (balance.amountNormalized > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
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

**File:** core/contracts/SpotEngineState.sol (L236-244)
```text
    function getStateAndBalance(uint32 productId, bytes32 subaccount)
        public
        view
        returns (State memory, Balance memory)
    {
        State memory state = states[productId];
        BalanceNormalized memory balance = balances[productId][subaccount];
        return (state, balanceNormalizedToBalance(state, balance));
    }
```

**File:** core/contracts/SpotEngineState.sol (L246-254)
```text
    function getBalance(uint32 productId, bytes32 subaccount)
        public
        view
        returns (Balance memory)
    {
        State memory state = states[productId];
        BalanceNormalized memory balance = balances[productId][subaccount];
        return balanceNormalizedToBalance(state, balance);
    }
```

**File:** core/contracts/SpotEngine.sol (L243-283)
```text
    function socializeSubaccount(bytes32 subaccount) external {
        require(msg.sender == address(_clearinghouse), ERR_UNAUTHORIZED);

        uint32[] memory _productIds = getProductIds();
        for (uint128 i = 0; i < _productIds.length; ++i) {
            uint32 productId = _productIds[i];

            State memory state = states[productId];
            Balance memory balance = balanceNormalizedToBalance(
                state,
                balances[productId][subaccount]
            );
            if (balance.amount < 0) {
                int128 totalDeposited = state.totalDepositsNormalized.mul(
                    state.cumulativeDepositsMultiplierX18
                );

                state.cumulativeDepositsMultiplierX18 = (totalDeposited +
                    balance.amount).div(state.totalDepositsNormalized);

                require(state.cumulativeDepositsMultiplierX18 > 0);

                state.totalBorrowsNormalized += balance.amount.div(
                    state.cumulativeBorrowsMultiplierX18
                );

                _setBalanceAndUpdateBitmap(
                    productId,
                    subaccount,
                    BalanceNormalized({amountNormalized: 0})
                );
                _setState(productId, state);
            }
        }
    }

    function manualAssert(bytes[] calldata _states) external view {
        for (uint128 i = 0; i < _states.length; ++i) {
            uint32 productId = productIds[i];
            require(
                keccak256(abi.encode(states[productId])) ==
```

**File:** core/contracts/BaseEngine.sol (L157-177)
```text
    function _calculateProductHealth(
        uint32 productId,
        bytes32 subaccount,
        IProductEngine.HealthType healthType
    ) internal returns (int128 health) {
        RiskHelper.Risk memory risk = _risk(productId);
        (int128 amount, int128 quoteAmount) = _getBalance(
            productId,
            subaccount
        );
        int128 weight = RiskHelper._getWeightX18(risk, amount, healthType);
        health += quoteAmount;

        if (amount != 0) {
            if (weight == 2 * ONE) {
                return -INF;
            }
            health += amount.mul(weight).mul(risk.priceX18);
            emit PriceQuery(productId);
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L71-84)
```text
    function getHealth(bytes32 subaccount, IProductEngine.HealthType healthType)
        public
        returns (int128 health)
    {
        ISpotEngine spotEngine = _spotEngine();
        IPerpEngine perpEngine = _perpEngine();

        health = spotEngine.getHealthContribution(subaccount, healthType);
        // min health means that it is attempting to borrow a spot that exists outside
        // of the risk system -- return min health to error out this action
        if (health == -INF) {
            return health;
        }
        health += perpEngine.getHealthContribution(subaccount, healthType);
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
