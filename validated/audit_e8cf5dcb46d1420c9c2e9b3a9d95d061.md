### Title
Spot Interest and Perp Funding Accrue During Mandatory Slow-Mode Delay While Position Closure Is Unavailable via Slow Mode, Enabling Forced Liquidation — (`core/contracts/EndpointTx.sol`, `core/contracts/SpotEngineState.sol`, `core/contracts/PerpEngineState.sol`)

---

### Summary

Nado's slow-mode escape hatch is the only on-chain mechanism for users to act without sequencer cooperation. However, `processSlowModeTransactionImpl` does not include `MatchOrders` or `CloseIsolatedSubaccount`, so users with open perp positions or spot borrows cannot close or reduce those positions via slow mode. Meanwhile, the sequencer continues to submit `SpotTick` and `PerpTick` transactions that accrue interest and funding on all open positions. The mandatory 3-day slow-mode delay creates a window during which a censored user's health silently deteriorates, potentially forcing liquidation before any slow-mode protective action can execute.

---

### Finding Description

**Interest and funding accrual are sequencer-only operations.**

`SpotEngineState.updateStates(dt)` and `PerpEngineState.updateStates(dt, avgPriceDiffs)` are both gated with `onlyEndpoint` and are triggered exclusively by the sequencer via `SpotTick` and `PerpTick` transaction types inside `processTransactionImpl`, which is only reachable through `submitTransactionsChecked` (requires `msg.sender == sequencer`). [1](#0-0) [2](#0-1) [3](#0-2) 

**Position closure is absent from slow mode.**

`processSlowModeTransactionImpl` handles: `DepositCollateral`, `WithdrawCollateral`, `LinkSigner`, `DepositInsurance`, and several admin-only types. It does **not** handle `MatchOrders`, `MatchOrdersWithAmount`, or `CloseIsolatedSubaccount`. Any attempt to submit those types falls through to `revert()`. [4](#0-3) 

**The slow-mode delay is a mandatory 3-day window.**

Every slow-mode transaction is timestamped with `block.timestamp + SLOW_MODE_TX_DELAY` and cannot be executed by anyone (including the user themselves) until that delay expires. [5](#0-4) 

**Failed slow-mode transactions are silently dropped.**

`_executeSlowModeTransaction` wraps `processSlowModeTransaction` in a try/catch. If a user's queued `WithdrawCollateral` or `DepositCollateral` fails (e.g., because health has deteriorated past the threshold during the delay), the transaction is consumed and discarded with no refund of the slow-mode fee. [6](#0-5) 

---

### Impact Explanation

A user with an open perp position or a negative spot balance (borrow) who is being censored by the sequencer has no on-chain path to close or reduce that position. Their only recourse is slow mode, but slow mode cannot submit `MatchOrders`. The sequencer, while censoring the user's normal transactions, continues to submit `PerpTick` and `SpotTick` ticks that compound funding and interest against the user's position. After 3 days the user can execute their queued deposit, but:

1. The accrued funding/interest may have already pushed maintenance health below zero, making the account liquidatable before the deposit executes.
2. Even if the deposit executes, the user still cannot close the perp position without sequencer cooperation.
3. A liquidator (whose `LiquidateSubaccount` transaction IS processed by the sequencer) can liquidate the account the moment the sequencer resumes normal operation, seizing the user's collateral.

The corrupted state delta is: `balance.vQuoteBalance` (perp) and `balance.amountNormalized` (spot borrow) grow adversely during the delay period, reducing `getHealth()` below the maintenance threshold. [7](#0-6) [8](#0-7) [9](#0-8) 

---

### Likelihood Explanation

The trigger requires the sequencer to selectively censor a user while continuing to submit global tick transactions. This is a realistic scenario: the sequencer is a single trusted operator, and the slow-mode mechanism exists precisely because sequencer censorship is an acknowledged threat model. The 3-day delay is hardcoded and cannot be shortened by the user. High-leverage perp positions or spot borrows at high utilization (high borrow rate) are most at risk because funding/interest compounds fastest in those conditions. [10](#0-9) [11](#0-10) 

---

### Recommendation

1. Add `MatchOrders` (or a dedicated `ClosePosition` transaction type) to `processSlowModeTransactionImpl` so that users can close perp positions without sequencer cooperation.
2. Alternatively, freeze interest and funding accrual for any subaccount that has a pending slow-mode transaction in the queue, analogous to the recommendation to freeze `pay_interest()` during a pause.
3. Do not make the interest/funding tick a prerequisite for processing slow-mode transactions, to avoid a scenario where a broken tick calculation blocks the escape hatch. [4](#0-3) [1](#0-0) 

---

### Proof of Concept

1. User opens a leveraged long perp position on product `P` via the sequencer (normal path).
2. Sequencer begins censoring the user: it stops including the user's `MatchOrders` (close) transactions in batches.
3. User calls `submitSlowModeTransaction` with a `WithdrawCollateral` payload to reduce exposure. The transaction is queued with `executableAt = block.timestamp + 3 days`.
4. Over the next 3 days, the sequencer continues to submit `PerpTick` batches. Each tick calls `perpEngine.updateStates(dt, avgPriceDiffs)`, advancing `cumulativeFundingLongX18` against the user's long position and reducing `balance.vQuoteBalance`.
5. After 3 days, the user calls `executeSlowModeTransaction()`. The `WithdrawCollateral` executes, but the health check at `Clearinghouse.getHealth()` may now return a value below zero (maintenance breach) because the accrued funding has eroded the position's value.
6. Alternatively, the withdrawal succeeds but the perp position remains open and underwater. The sequencer immediately submits a `LiquidateSubaccount` transaction for a liquidator, seizing the user's remaining collateral. [12](#0-11) [13](#0-12) [9](#0-8)

### Citations

**File:** core/contracts/EndpointTx.sol (L202-330)
```text
    function processSlowModeTransactionImpl(
        address sender,
        bytes calldata transaction
    ) public {
        IEndpoint.TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            IEndpoint.DepositCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositCollateral)
            );
            validateSender(txn.sender, sender);
            _recordSubaccount(txn.sender);
            clearinghouse.depositCollateral(txn);
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.WithdrawCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.WithdrawCollateral)
            );
            validateSender(txn.sender, sender);
            clearinghouse.withdrawCollateral(
                txn.sender,
                txn.productId,
                txn.amount,
                address(0),
                nSubmissions
            );
        } else if (txType == IEndpoint.TransactionType.DepositInsurance) {
            clearinghouse.depositInsurance(transaction);
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.LinkSigner memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.LinkSigner)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
        } else if (txType == IEndpoint.TransactionType.WithdrawInsurance) {
            clearinghouse.withdrawInsurance(transaction, nSubmissions);
        } else if (txType == IEndpoint.TransactionType.DelistProduct) {
            clearinghouse.delistProduct(transaction);
        } else if (txType == IEndpoint.TransactionType.DumpFees) {
            IOffchainExchange(offchainExchange).dumpFees();
            uint32[] memory spotIds = spotEngine.getProductIds();
            int128[] memory fees = new int128[](spotIds.length);
            for (uint256 i = 0; i < spotIds.length; i++) {
                fees[i] = sequencerFee[spotIds[i]];
                sequencerFee[spotIds[i]] = 0;
            }
            requireSubaccount(X_ACCOUNT);
            clearinghouse.claimSequencerFees(fees);
        } else if (txType == IEndpoint.TransactionType.RebalanceXWithdraw) {
            clearinghouse.rebalanceXWithdraw(transaction, nSubmissions);
        } else if (txType == IEndpoint.TransactionType.UpdateTierFeeRates) {
            IEndpoint.UpdateTierFeeRates memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.UpdateTierFeeRates)
            );
            IOffchainExchange(offchainExchange).updateTierFeeRates(txn);
        } else if (txType == IEndpoint.TransactionType.AddNlpPool) {
            IEndpoint.AddNlpPool memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.AddNlpPool)
            );
            addNlpPool(txn.owner, txn.balanceWeightX18);
        } else if (txType == IEndpoint.TransactionType.UpdateNlpPool) {
            IEndpoint.UpdateNlpPool memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.UpdateNlpPool)
            );
            updateNlpPool(txn.poolId, txn.owner, txn.balanceWeightX18);
        } else if (txType == IEndpoint.TransactionType.DeleteNlpPool) {
            IEndpoint.DeleteNlpPool memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DeleteNlpPool)
            );
            deleteNlpPool(txn.poolId);
        } else if (txType == IEndpoint.TransactionType.ForceRebalanceNlpPool) {
            IEndpoint.ForceRebalanceNlpPool memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.ForceRebalanceNlpPool)
            );
            clearinghouse.forceRebalanceNlpPool(
                nlpPools,
                txn.nlpPoolRebalanceX18
            );
        } else if (txType == IEndpoint.TransactionType.NlpProfitShare) {
            IEndpoint.NlpProfitShare memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.NlpProfitShare)
            );
            require(
                txn.poolId > 0 && txn.poolId < nlpPools.length,
                ERR_INVALID_NLP_POOL
            );
            require(
                nlpPools[txn.poolId].owner != address(0),
                ERR_INVALID_NLP_POOL
            );
            require(
                address(uint160(bytes20(txn.recipient))) ==
                    nlpPools[txn.poolId].owner,
                ERR_UNAUTHORIZED
            );
            requireSubaccount(txn.recipient);
            require(!RiskHelper.isIsolatedSubaccount(txn.recipient));
            clearinghouse.nlpProfitShare(
                nlpPools[txn.poolId].subaccount,
                txn.recipient,
                txn.amount
            );
        } else if (txType == IEndpoint.TransactionType.UpdateBuilder) {
            IOffchainExchange(offchainExchange).updateBuilder(transaction);
        } else if (txType == IEndpoint.TransactionType.ClaimBuilderFee) {
            IEndpoint.ClaimBuilderFee memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.ClaimBuilderFee)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            IOffchainExchange(offchainExchange).claimBuilderFee(
                txn.sender,
                txn.builderId
            );
        } else {
            revert();
        }
    }
```

**File:** core/contracts/EndpointTx.sol (L374-385)
```text
        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
        // TODO: to save on costs we could potentially just emit something
        // for now, we can just create a separate loop in the engine that queries the remote
        // sequencer for slow mode transactions, and ignore the possibility of a reorgy attack
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/EndpointTx.sol (L466-485)
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
        } else if (txType == IEndpoint.TransactionType.PerpTick) {
            IEndpoint.PerpTick memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.PerpTick)
            );
            Times memory t = times;
            uint128 dt = t.perpTime == 0 ? 0 : txn.time - t.perpTime;
            perpEngine.updateStates(dt, txn.avgPriceDiffs);
            t.perpTime = txn.time;
            times = t;
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

**File:** core/contracts/PerpEngineState.sol (L31-51)
```text
        int128 cumulativeFundingAmountX18 = (balance.amount > 0)
            ? state.cumulativeFundingLongX18
            : state.cumulativeFundingShortX18;
        int128 diffX18 = cumulativeFundingAmountX18 -
            balance.lastCumulativeFundingX18;
        int128 deltaQuote = vQuoteDelta - diffX18.mul(balance.amount);

        // apply delta
        balance.amount += balanceDelta;

        // apply vquote
        balance.vQuoteBalance += deltaQuote;

        // post update
        if (balance.amount > 0) {
            state.openInterest += balance.amount;
            balance.lastCumulativeFundingX18 = state.cumulativeFundingLongX18;
        } else {
            state.openInterest -= balance.amount;
            balance.lastCumulativeFundingX18 = state.cumulativeFundingShortX18;
        }
```

**File:** core/contracts/PerpEngineState.sol (L103-144)
```text
    function updateStates(uint128 dt, int128[] calldata avgPriceDiffs)
        external
        onlyEndpoint
    {
        int128 dtX18 = int128(dt).fromInt();
        for (uint32 i = 0; i < avgPriceDiffs.length; i++) {
            uint32 productId = productIds[i];
            State memory state = states[productId];
            if (state.openInterest == 0) {
                continue;
            }
            require(dt < 7 * SECONDS_PER_DAY, ERR_INVALID_TIME);
            {
                int128 indexPriceX18 = _risk(productId).priceX18;

                // cap this price diff
                int128 priceDiffX18 = avgPriceDiffs[i];

                int128 maxPriceDiff = MAX_DAILY_FUNDING_RATE.mul(indexPriceX18);

                if (priceDiffX18.abs() > maxPriceDiff) {
                    // Proper sign
                    priceDiffX18 = (priceDiffX18 > 0)
                        ? maxPriceDiff
                        : -maxPriceDiff;
                }

                int128 paymentAmount = priceDiffX18.mul(dtX18).div(ONE_DAY_X18);
                state.cumulativeFundingLongX18 += paymentAmount;
                state.cumulativeFundingShortX18 += paymentAmount;

                emit FundingPayment(
                    productId,
                    dt,
                    state.openInterest,
                    paymentAmount
                );
            }
            _setState(productId, state);
            emit PriceQuery(productId);
        }
    }
```

**File:** core/contracts/Endpoint.sol (L205-228)
```text
            uint256 gasRemaining = gasleft();
            // solhint-disable-next-line no-empty-blocks
            try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
                // we need to differentiate between a revert and an out of gas
                // the issue is that in evm every inner call only 63/64 of the
                // remaining gas in the outer frame is forwarded. as a result
                // the amount of gas left for execution is (63/64)**len(stack)
                // and you can get an out of gas while spending an arbitrarily
                // low amount of gas in the final frame. we use a heuristic
                // here that isn't perfect but covers our cases.
                // having gasleft() <= gasRemaining / 2 buys us 44 nested calls
                // before we miss out of gas errors; 1/2 ~= (63/64)**44
                // this is good enough for our purposes

                if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
                    // solhint-disable-next-line no-inline-assembly
                    assembly {
                        invalid()
                    }
                }

                // try return funds now removed
            }
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

**File:** core/contracts/Endpoint.sol (L271-294)
```text
    function submitTransactionsChecked(
        uint64 idx,
        bytes[] calldata transactions,
        bytes32 e,
        bytes32 s,
        uint8 signerBitmask
    ) external {
        validateSubmissionIdx(idx);
        require(msg.sender == sequencer);
        // TODO: if one of these transactions fails this means the sequencer is in an error state
        // we should probably record this, and engage some sort of recovery mode

        bytes32 digest = keccak256(abi.encode(idx));
        for (uint256 i = 0; i < transactions.length; ++i) {
            digest = keccak256(abi.encodePacked(digest, transactions[i]));
        }
        verifier.requireValidSignature(digest, e, s, signerBitmask);

        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            nSubmissions += 1;
        }
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
