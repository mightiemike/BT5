### Title
Bad Debt Not Socialized Among All Depositors Before Withdrawal — Early Withdrawers Escape Loss at Late Withdrawers' Expense (`core/contracts/Clearinghouse.sol` / `core/contracts/SpotEngine.sol`)

---

### Summary

When a subaccount accumulates bad debt (negative QUOTE balance that exceeds the insurance fund), the loss is not immediately distributed proportionally among all depositors. The `socializeSubaccount` mechanism that reduces `cumulativeDepositsMultiplierX18` is only triggered during `_finalizeSubaccount`, which requires a separate liquidation finalization transaction. In the window between bad debt accumulation and finalization, depositors can withdraw at full internal balance value. The `assertUtilization` check only prevents the *last* depositor from over-withdrawing — it does not enforce proportional loss distribution. The last depositor to withdraw bears the entire bad debt loss while early withdrawers suffer none.

---

### Finding Description

**Socialization is deferred, not immediate.**

When a bankrupt subaccount is finalized in `_finalizeSubaccount` (ClearinghouseLiq.sol), the call chain is:

1. `perpEngine.socializeSubaccount(txn.liquidatee, v.insurance)` — writes off perp bad debt
2. If `v.insurance <= 0`: `spotEngine.socializeSubaccount(txn.liquidatee)` — reduces `cumulativeDepositsMultiplierX18` for QUOTE [1](#0-0) 

The `socializeSubaccount` in `SpotEngine` correctly reduces `cumulativeDepositsMultiplierX18` proportionally across all depositors: [2](#0-1) 

**But this only runs after `_finalizeSubaccount` is called.** `_finalizeSubaccount` is only triggered when a liquidator submits a `LiquidateSubaccount` transaction with `productId == type(uint32).max` — a separate, later transaction.

**The `assertUtilization` check does not enforce proportional distribution.**

`withdrawCollateral` calls `assertUtilization` after updating the withdrawer's balance: [3](#0-2) 

`assertUtilization` only checks `totalDeposits >= totalBorrows`: [4](#0-3) 

**Concrete example with two depositors (User A: 500 QUOTE, User B: 500 QUOTE) and 100 QUOTE bad debt:**

| Step | totalDeposits | totalBorrows | assertUtilization |
|---|---|---|---|
| Initial | 1000 | 100 (bad debt) | 1000 ≥ 100 ✓ |
| User A withdraws 500 | 500 | 100 | 500 ≥ 100 ✓ |
| User B withdraws 500 | 0 | 100 | 0 ≥ 100 **FAILS** |

User B can only withdraw 400. User A exits at full value. The entire 100 QUOTE bad debt is borne by User B alone.

**Attacker-controlled entry path:**

All withdrawals flow through `Endpoint → EndpointTx → Clearinghouse.withdrawCollateral`. A depositor submits a signed `WithdrawCollateral` transaction to the sequencer. The sequencer processes transactions in submission order. A depositor who monitors the chain for bad debt events can submit a withdrawal before the liquidation finalization transaction is submitted, racing ahead of other depositors and the `_finalizeSubaccount` call. [5](#0-4) 

---

### Impact Explanation

- **Severe fund loss** for the last depositor(s) to withdraw: they bear 100% of the bad debt instead of their proportional share.
- **Early depositors profit at the expense of late depositors**: they withdraw at full internal balance value with zero loss.
- **Unfair risk distribution** undermines protocol trust and LP incentives.
- The corrupted state is the QUOTE `cumulativeDepositsMultiplierX18` — it is applied too late (post-finalization) rather than at the moment bad debt is confirmed, allowing the pre-loss multiplier to be used for early withdrawals.

---

### Likelihood Explanation

**Medium.** Bad debt requires the insurance fund to be depleted, which is a non-trivial precondition. However, once bad debt exists:
- Any depositor who monitors on-chain events (e.g., `Liquidation` events, negative QUOTE balances) can detect the window.
- The race is purely a matter of submitting a withdrawal transaction before the liquidation finalization transaction is processed by the sequencer.
- No special privileges are required — any depositor can exploit this through the standard `WithdrawCollateral` flow.

---

### Recommendation

Socialize bad debt **immediately** when it is confirmed (i.e., when the insurance fund is insufficient to cover a subaccount's negative balance), rather than deferring it to `_finalizeSubaccount`. One approach: call `spotEngine.socializeSubaccount` as soon as `insurance` drops to or below zero during `_handleLiquidationPayment`, before any subsequent withdrawals can be processed. Alternatively, add a check in `withdrawCollateral` that blocks withdrawals when `totalBorrows > totalDeposits` for QUOTE (i.e., when unresolved bad debt exists), forcing socialization to occur first.

---

### Proof of Concept

1. User A and User B each deposit 500 QUOTE into the protocol via `Endpoint → depositCollateral`.
2. User X deposits collateral worth 200 QUOTE and borrows 180 QUOTE (90% LTV).
3. Collateral price drops to 60 QUOTE. Insurance fund is 0 (or depleted). Bad debt = 180 - 60 = 120 QUOTE.
4. A liquidator liquidates User X's collateral for 60 QUOTE. User X's QUOTE balance = -180 + 60 = -120 (bad debt). `_finalizeSubaccount` has NOT been called yet.
5. At this point: `totalDeposits = 1000`, `totalBorrows = 120`. `assertUtilization` passes (1000 ≥ 120).
6. User A submits `WithdrawCollateral(500)` to the sequencer. It is processed. `assertUtilization` passes (500 ≥ 120). User A receives 500 QUOTE.
7. Liquidator submits `LiquidateSubaccount(productId=uint32.max)` to finalize. `_finalizeSubaccount` runs, `socializeSubaccount` reduces `cumulativeDepositsMultiplierX18`. User B's balance is now reduced to ~380 QUOTE (500 - 120).
8. User B withdraws and receives only ~380 QUOTE, bearing the full 120 QUOTE bad debt loss alone. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/ClearinghouseLiq.sol (L386-412)
```text
        v.insurance = perpEngine.socializeSubaccount(
            txn.liquidatee,
            v.insurance
        );

        // we can assure that quoteBalance must be non positive, because if quoteBalance.amount > 0,
        // there must be 1) no negative pnl in perps, and 2) no liabilities in spot after above actions.
        // however, in this case the liquidatee must be healthy and cannot pass the health check at
        // the beginning.
        int128 insuranceCover = MathHelper.min(
            v.insurance,
            -quoteBalance.amount
        );
        if (insuranceCover > 0) {
            v.insurance -= insuranceCover;
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.liquidatee,
                insuranceCover
            );
        }
        if (v.insurance <= 0) {
            spotEngine.socializeSubaccount(txn.liquidatee);
        }
        v.insurance += lastLiquidationFees;
        insurance = v.insurance;
        return true;
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

**File:** core/contracts/SpotEngine.sol (L243-277)
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
