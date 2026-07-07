### Title
Stale `cumulativeBorrowsMultiplierX18` in `SpotEngineState.getBalance` Understates Borrower Debt, Enabling Liquidation Bypass — (`core/contracts/SpotEngineState.sol`)

---

### Summary

`SpotEngineState.getBalance` converts normalized balances to real balances using the stored `cumulativeBorrowsMultiplierX18` (or `cumulativeDepositsMultiplierX18`) directly from `states[productId]`, without first calling `_updateState` to accrue pending interest. Between sequencer-submitted `updateStates` calls, these multipliers are stale. As a result, borrowers' actual debts are systematically understated in every health check, allowing undercollateralized positions to appear healthy and bypass liquidation.

---

### Finding Description

Nado's SpotEngine tracks balances in normalized form (`amountNormalized`) and converts them to real amounts by multiplying by a cumulative interest multiplier — exactly analogous to how Derby's Compound provider converts cToken balances using an exchange rate.

The multipliers are updated only when the sequencer explicitly submits an `updateStates` transaction, which calls `_updateState`: [1](#0-0) 

Between those calls, `states[productId].cumulativeBorrowsMultiplierX18` is stale (lower than the true current value, since it only grows). Every call to `getBalance` reads this stale state directly: [2](#0-1) 

`balanceNormalizedToBalance` then multiplies by the stale multiplier: [3](#0-2) 

For a borrower (`amountNormalized < 0`), the stale `cumulativeBorrowsMultiplierX18` is smaller than the true current value, so the reported debt is **less than the actual debt**. The gap grows with every second that passes since the last `updateStates` call.

This stale balance propagates directly into the liquidation health check:

- `ClearinghouseLiq.liquidateSubaccountImpl` calls `isUnderMaintenance` as its first gate [4](#0-3) 
- `isUnderMaintenance` calls `getHealthFromClearinghouse` → `Clearinghouse.getHealth` [5](#0-4) 
- `Clearinghouse.getHealth` calls `spotEngine.getHealthContribution` [6](#0-5) 
- `getHealthContribution` (via `BaseEngine`) calls `_getBalance` → `SpotEngineState.getBalance` → `balanceNormalizedToBalance` with the stale state [7](#0-6) 

The same stale path is used in `withdrawCollateral`'s health check and `transferQuote`'s health check: [8](#0-7) [9](#0-8) 

This is structurally identical to Derby M-35: Derby used `exchangeRateStored` (cached) instead of `exchangeRateCurrent` (live); Nado reads `states[productId].cumulativeBorrowsMultiplierX18` (cached) instead of computing the current accrued value.

---

### Impact Explanation

**Liquidation bypass**: A borrower's debt is understated by the interest accrued since the last `updateStates` call. If the sequencer submits `updateStates` once per hour and the annualized borrow rate is 10%, a $1M position accumulates ~$11 of understated debt per hour — small per interval but compounding. More critically, during periods of high market volatility when liquidations are most needed, the sequencer may not have submitted `updateStates` recently, and the stale multiplier causes the liquidation gate (`isUnderMaintenance`) to return `false` for a position that is genuinely undercollateralized. The liquidation call reverts with `ERR_NOT_LIQUIDATABLE`, leaving bad debt to accumulate.

**Depositor balance understatement**: Depositors' balances are also understated (stale `cumulativeDepositsMultiplierX18`), causing their health to appear lower than actual. This can incorrectly block valid withdrawals via `withdrawCollateral`.

---

### Likelihood Explanation

The gap between `updateStates` calls is a normal operational condition, not an edge case. Any liquidation attempt that occurs before the sequencer's next `updateStates` transaction uses stale multipliers. The longer the interval and the higher the borrow rate, the larger the understatement. This is a structural, always-present issue — not a race condition or edge case.

---

### Recommendation

`getBalance` and `getStateAndBalance` in `SpotEngineState` should compute the current accrued multiplier before converting normalized balances, rather than reading the stored (stale) state. Concretely, a view-only version of `_updateState` that accepts the current timestamp (from `block.timestamp` or the oracle time) should be applied before any balance-to-health conversion. Alternatively, `updateStates` should be enforced as the first transaction in every sequencer batch before any health-sensitive operation.

---

### Proof of Concept

1. Alice opens a leveraged spot position, borrowing USDC against ETH collateral. Her `amountNormalized` for USDC is `-N` (negative = borrow).
2. One hour passes. The sequencer has not yet submitted `updateStates`. `cumulativeBorrowsMultiplierX18` in `states[QUOTE_PRODUCT_ID]` is stale — it reflects the multiplier from one hour ago.
3. ETH price drops. Alice's position is now undercollateralized at the true current borrow amount.
4. Bob (liquidator) calls `liquidateSubaccount` targeting Alice.
5. `liquidateSubaccountImpl` calls `isUnderMaintenance(alice)` → `getHealth` → `spotEngine.getHealthContribution` → `getBalance(QUOTE_PRODUCT_ID, alice)`.
6. `getBalance` returns `balanceNormalizedToBalance(states[QUOTE_PRODUCT_ID], ...)`, using the stale `cumulativeBorrowsMultiplierX18`. Alice's reported debt is smaller than her actual debt.
7. `getHealth` returns a value ≥ 0 (appears healthy). `isUnderMaintenance` returns `false`.
8. The call reverts with `ERR_NOT_LIQUIDATABLE`. Alice's undercollateralized position is not liquidated.
9. The sequencer eventually submits `updateStates`. Now Alice's true debt is reflected, but the window of bad debt exposure has already passed — or worse, the position has deteriorated further. [2](#0-1) [3](#0-2) [10](#0-9)

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

**File:** core/contracts/ClearinghouseLiq.sol (L598-607)
```text
    function liquidateSubaccountImpl(IEndpoint.LiquidateSubaccount calldata txn)
        external
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.sender != txn.liquidatee, ERR_UNAUTHORIZED);
        require(isUnderMaintenance(txn.liquidatee), ERR_NOT_LIQUIDATABLE);
        require(
            txn.liquidatee != X_ACCOUNT && txn.liquidatee != N_ACCOUNT,
            ERR_NOT_LIQUIDATABLE
        );
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

**File:** core/contracts/Clearinghouse.sol (L247-249)
```text
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -toTransfer);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.recipient, toTransfer);
        require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```
