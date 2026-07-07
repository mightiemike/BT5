### Title
`SpotEngineState::getStateAndBalance()` / `getBalance()` Return Stale Cumulative Interest Multipliers, Corrupting Health and Utilization Checks — (`core/contracts/SpotEngineState.sol`)

---

### Summary

`SpotEngineState.getStateAndBalance()` and `getBalance()` return the stored `State` struct — which contains `cumulativeDepositsMultiplierX18` and `cumulativeBorrowsMultiplierX18` — directly from storage without first calling `_updateState()` to accrue pending interest. These multipliers are the Nado equivalent of RAAC's `liquidityIndex`: they grow over time as interest accrues, and every real balance is computed as `amountNormalized * cumulativeMultiplierX18`. Because the getters skip the accrual step, every downstream consumer — including the health engine and the utilization guard — operates on stale data.

---

### Finding Description

`SpotEngineState._updateState()` computes the borrow-rate multiplier for elapsed time `dt` and advances both `cumulativeBorrowsMultiplierX18` and `cumulativeDepositsMultiplierX18`. It is only invoked from `updateStates()`, which carries the `onlyEndpoint` modifier and is therefore exclusively callable by the sequencer. [1](#0-0) 

Between two sequencer `updateStates()` calls the multipliers stored in `states[productId]` are frozen at their last-written values.

`getStateAndBalance()` and `getBalance()` read those frozen values directly: [2](#0-1) 

`balanceNormalizedToBalance()` then multiplies the stored normalized amount by the stale multiplier: [3](#0-2) 

Because the multiplier has not been advanced, the returned `balance.amount` is smaller in absolute value than the true current balance for both depositors and borrowers.

`_getBalance()` in `SpotEngineState` delegates to `getBalance()`: [4](#0-3) 

`BaseEngine._calculateProductHealth()` calls `_getBalance()` to obtain the amount used in the health formula: [5](#0-4) 

`getHealthContribution()` aggregates these per-product health values: [6](#0-5) 

`Clearinghouse.getHealth()` calls `spotEngine.getHealthContribution()` and the result drives every critical safety gate in the protocol: [7](#0-6) 

`withdrawCollateral()` enforces health after deducting the withdrawal: [8](#0-7) 

`withdrawCollateral()` also calls `assertUtilization()`, which itself reads `getStateAndBalance()` with the same stale state: [9](#0-8) 

`ClearinghouseLiq.isUnderMaintenance()` — the liquidation eligibility gate — also resolves through `getHealth()`: [10](#0-9) 

---

### Impact Explanation

Because `cumulativeBorrowsMultiplierX18` is stale (lower than its true current value), a borrower's liability `amountNormalized * stale_multiplier` is understated. The health formula therefore returns a value that is **higher than the true health**. Concretely:

1. **Liquidation bypass**: `isUnderMaintenance()` returns `false` for a subaccount that is genuinely under-water, blocking the liquidator's `liquidateSubaccountImpl()` call and leaving bad debt unaddressed.
2. **Over-withdrawal**: `withdrawCollateral()` passes the `getHealth() >= 0` check with an inflated health figure, allowing a borrower to extract collateral that should be locked.
3. **Utilization guard bypass**: `assertUtilization()` computes `totalBorrows` from the stale multiplier, so the `totalDeposits >= totalBorrows` invariant can pass even when true borrows exceed deposits.

The corrupted state delta is the subaccount's effective quote balance and the protocol's solvency invariant (`totalDeposits >= totalBorrows`).

---

### Likelihood Explanation

The stale window is the interval between consecutive sequencer `updateStates()` submissions. In normal operation this is short, but:

- There is no on-chain upper bound on this interval.
- A user can observe the last `updateStates()` timestamp and submit a withdrawal or monitor liquidation eligibility during the stale window.
- The discrepancy grows monotonically with elapsed time and with utilization (higher utilization → higher borrow rate → faster multiplier growth → larger stale gap).

No privileged access is required beyond submitting a standard signed transaction through the supported `Endpoint` entrypoint.

---

### Recommendation

Mirror the pattern used in `PerpEngineState.getStateAndBalance()`, which applies a zero-delta `_updateBalance()` before returning, to compute the current value without writing to storage. For `SpotEngineState`, introduce an analogous pure helper that projects the current multipliers forward by `block.timestamp - lastUpdateTime` and apply it inside `getStateAndBalance()` and `getBalance()`:

```diff
function getStateAndBalance(uint32 productId, bytes32 subaccount)
    public
    view
    returns (State memory, Balance memory)
{
    State memory state = states[productId];
+   _projectState(state, block.timestamp - lastUpdateTime[productId]);
    BalanceNormalized memory balance = balances[productId][subaccount];
    return (state, balanceNormalizedToBalance(state, balance));
}
```

where `_projectState` is a `pure`/`view` version of `_updateState` that does not write fees or emit events. Alternatively, ensure `assertUtilization()` and all health-path callers always receive a freshly projected state.

---

### Proof of Concept

1. Deploy `SpotEngine` and `Clearinghouse` with a non-zero interest floor config.
2. Deposit collateral and borrow against it so utilization > 0.
3. Advance time by N seconds **without** calling `updateStates()`.
4. Call `Clearinghouse.getHealth(subaccount, MAINTENANCE)` — it returns a value computed from the stale multiplier.
5. Manually compute the true health using the projected multiplier (`(1 + borrowRate)^N * cumulativeBorrowsMultiplierX18`).
6. Observe that step 4 returns a health value higher than step 5, confirming that a subaccount that should be under maintenance health passes the check, and that `withdrawCollateral()` would succeed when it should revert.

### Citations

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

**File:** core/contracts/SpotEngineState.sol (L236-254)
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

**File:** core/contracts/SpotEngineState.sol (L256-263)
```text
    function _getBalance(uint32 productId, bytes32 subaccount)
        internal
        view
        override
        returns (int128, int128)
    {
        return (getBalance(productId, subaccount).amount, 0);
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

**File:** core/contracts/BaseEngine.sol (L112-135)
```text
    function getHealthContribution(
        bytes32 subaccount,
        IProductEngine.HealthType healthType
    ) public returns (int128 health) {
        uint32 maxBitmapIndex = _getMaxProductId() / 256;

        for (
            uint32 bitmapIndex = 0;
            bitmapIndex <= maxBitmapIndex;
            bitmapIndex++
        ) {
            uint256 bitmapChunk = _getBitmapChunk(subaccount, bitmapIndex);
            if (bitmapChunk == 0) {
                continue;
            }

            health += _processBitmapChunk(
                bitmapChunk,
                bitmapIndex,
                subaccount,
                healthType
            );
        }
    }
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

**File:** core/contracts/Clearinghouse.sol (L415-420)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
```

**File:** core/contracts/SpotEngine.sol (L232-241)
```text
    function assertUtilization(uint32 productId) external view {
        (State memory _state, ) = getStateAndBalance(productId, X_ACCOUNT);
        int128 totalDeposits = _state.totalDepositsNormalized.mul(
            _state.cumulativeDepositsMultiplierX18
        );
        int128 totalBorrows = _state.totalBorrowsNormalized.mul(
            _state.cumulativeBorrowsMultiplierX18
        );
        require(totalDeposits >= totalBorrows, ERR_MAX_UTILIZATION);
    }
```

**File:** core/contracts/ClearinghouseLiq.sol (L51-58)
```text
    function isUnderMaintenance(bytes32 subaccount) internal returns (bool) {
        // Weighted maintenance health < 0
        return
            getHealthFromClearinghouse(
                subaccount,
                IProductEngine.HealthType.MAINTENANCE
            ) < 0;
    }
```
