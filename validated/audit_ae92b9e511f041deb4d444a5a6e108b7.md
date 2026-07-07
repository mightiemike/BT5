### Title
Stale Interest Multipliers Used in Deposit, Withdrawal, and Liquidation Paths Without Calling `updateStates` First - (File: `core/contracts/Clearinghouse.sol`, `core/contracts/ClearinghouseLiq.sol`)

---

### Summary

`SpotEngine` tracks interest accrual through `cumulativeDepositsMultiplierX18` and `cumulativeBorrowsMultiplierX18`, updated only when `SpotEngine.updateStates` is called via a `SpotTick` transaction. `Clearinghouse.depositCollateral`, `Clearinghouse.withdrawCollateral`, and `ClearinghouseLiq.liquidateSubaccountImpl` all read and write balances using these multipliers without first calling `updateStates`. When multipliers are stale, balance accounting and health checks are computed on outdated values, enabling incorrect asset accounting and excess collateral withdrawal.

---

### Finding Description

`SpotEngine` stores balances in normalized form (`amountNormalized`) and converts to actual amounts by multiplying by `cumulativeDepositsMultiplierX18` or `cumulativeBorrowsMultiplierX18`. These multipliers grow over time to reflect accrued interest and are updated exclusively in `SpotEngine.updateStates`, which is only called when the sequencer submits a `SpotTick` transaction. [1](#0-0) 

`SpotEngine.updateStates` is never called inside `Clearinghouse.depositCollateral`, `Clearinghouse.withdrawCollateral`, or `ClearinghouseLiq.liquidateSubaccountImpl`. Each of these functions reads `states[productId]` directly from storage, which may hold stale multipliers. [2](#0-1) 

`SpotEngine._updateBalanceNormalized` uses the stale `cumulativeDepositsMultiplierX18` / `cumulativeBorrowsMultiplierX18` to denormalize the existing balance and renormalize the post-delta amount: [3](#0-2) 

`SpotEngine.getBalance` (used in health checks) also reads the stored state directly without triggering an update: [4](#0-3) 

The health check in `withdrawCollateral` therefore evaluates a borrower's debt using a stale (lower-than-true) `cumulativeBorrowsMultiplierX18`: [5](#0-4) 

The same stale-state problem applies to `depositCollateral`: [6](#0-5) 

And to `liquidateSubaccountImpl`, which reads spot balances and computes liquidation amounts without refreshing multipliers: [7](#0-6) 

---

### Impact Explanation

**Excess collateral withdrawal by borrowers:** When `cumulativeBorrowsMultiplierX18` is stale (lower than the true accrued value), a borrower's debt is understated in the health check. The check passes when it should fail, allowing the borrower to withdraw collateral they are not entitled to. The magnitude of the excess is proportional to `borrow_balance × borrow_rate × time_since_last_SpotTick`.

**Free interest for depositors:** When `cumulativeDepositsMultiplierX18` is stale, a new depositor's `amountNormalized` is computed by dividing by a smaller-than-true multiplier, yielding more normalized units than correct. When `updateStates` is later called with the full elapsed `dt`, the depositor's balance grows by the full interest rate applied to their inflated normalized amount, giving them interest for a period before they deposited.

**Incorrect liquidation amounts:** Liquidation health checks and balance reads in `ClearinghouseLiq` use stale multipliers, potentially allowing under-liquidation (liquidatee appears healthier than they are) or incorrect liquidation payment computation.

---

### Likelihood Explanation

The slow mode execution path is the concrete trigger. `Endpoint.executeSlowModeTransaction()` is callable by any unprivileged user after the 3-day timeout: [8](#0-7) 

This path calls `clearinghouse.depositCollateral` or `clearinghouse.withdrawCollateral` directly, with no `updateStates` call anywhere in the call chain. If the sequencer has not submitted a `SpotTick` recently (e.g., due to downtime, congestion, or deliberate censorship), multipliers will be stale at the time of slow mode execution. A user who is being censored by the sequencer — the exact scenario slow mode is designed for — is also the user most likely to execute against stale multipliers, since the sequencer would not have been submitting `SpotTick` updates during the censorship window.

---

### Recommendation

- **Short term:** At the start of `Clearinghouse.depositCollateral`, `Clearinghouse.withdrawCollateral`, and `ClearinghouseLiq.liquidateSubaccountImpl`, call `spotEngine.updateStates` with the elapsed time (`block.timestamp - lastSpotTime` or equivalent) before any balance read or write. Expose a view on the last `spotTime` from `EndpointStorage` to enable this.
- **Long term:** Redesign `SpotEngine.getBalance` and `SpotEngine.updateBalance` to accept or internally compute the current time and apply the pending interest before returning or modifying any balance, making it structurally impossible to read a stale balance.

---

### Proof of Concept

1. Alice has a borrow position: `amountNormalized = -1000`, `cumulativeBorrowsMultiplierX18 = 1.0e18` (true debt = 1000 USDC).
2. One day passes. True `cumulativeBorrowsMultiplierX18` should be `≈1.0003e18` (0.03% daily rate), making true debt ≈ 1000.3 USDC. But no `SpotTick` has been submitted.
3. Alice submits a `WithdrawCollateral` slow mode transaction for maximum allowed collateral.
4. After 3 days, Alice (or anyone) calls `Endpoint.executeSlowModeTransaction()`.
5. `Clearinghouse.withdrawCollateral` calls `spotEngine.updateBalance` and then `getHealth`, both using the stale `cumulativeBorrowsMultiplierX18 = 1.0e18`.
6. Alice's debt is evaluated as 1000 USDC instead of the true ≈1000.3 USDC. The health check passes with 0.3 USDC less debt than reality.
7. Alice withdraws collateral that should have been blocked by the accrued interest, extracting value from the protocol's depositors. [2](#0-1) [9](#0-8)

### Citations

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

**File:** core/contracts/ClearinghouseLiq.sol (L598-647)
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
        require(
            txn.productId != QUOTE_PRODUCT_ID,
            ERR_INVALID_LIQUIDATION_PARAMS
        );

        ISpotEngine spotEngine = ISpotEngine(
            address(engineByType[IProductEngine.EngineType.SPOT])
        );
        IPerpEngine perpEngine = IPerpEngine(
            address(engineByType[IProductEngine.EngineType.PERP])
        );

        if (_finalizeSubaccount(txn, spotEngine, perpEngine)) {
            if (RiskHelper.isIsolatedSubaccount(txn.liquidatee)) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.liquidatee);
            }
            return;
        }

        if (
            (txn.amount < 0) &&
            (txn.isEncodedSpread ||
                address(productToEngine[txn.productId]) == address(spotEngine))
        ) {
            // when it's spread or spot liquidation, we need to make sure the liquidatee has
            // enough quote to buyback the liquidated amount.
            _assertCanLiquidateLiability(txn, spotEngine, perpEngine);
            _settlePositivePerpPnl(txn, spotEngine, perpEngine);
        }

        _assertLiquidationAmount(txn, spotEngine, perpEngine);

        // beyond this point, we can be sure that we can liquidate the entire
        // liquidation amount knowing that the insurance fund will remain solvent
        // subsequently we can just blast the remainder of the liquidation and
        // cover the quote balance from the insurance fund at the end
        _handleLiquidationPayment(txn, spotEngine, perpEngine);
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
