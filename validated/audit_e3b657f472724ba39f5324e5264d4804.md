### Title
`minDepositRateX18` Accumulates Unconditionally at Zero Utilization, Inflating Depositor Balances Without a Funding Source — (`File: core/contracts/SpotEngineState.sol`)

---

### Summary

`SpotEngineState._updateState` correctly zeroes the borrow rate when `utilizationRatioX18 == 0`, but the `minDepositRateX18` block runs unconditionally regardless of utilization. When a spot product has `minDepositRateX18 > 0` and no active borrowers, `cumulativeDepositsMultiplierX18` grows every tick with no corresponding borrower paying for it, creating unbacked value and corrupting the protocol's core solvency invariant.

---

### Finding Description

In `SpotEngineState._updateState`, the protocol explicitly handles zero utilization for the main borrow rate:

```solidity
if (utilizationRatioX18 == 0) {
    borrowerRateX18 = 0;
}
```

The comment even documents the intent: *"adding a product at the beginning of time and not using it until time T results in the same state as adding the product at time T."*

However, the `minDepositRateX18` block immediately below has no equivalent guard:

```solidity
// apply the min deposit rate
if (minDepositRateX18 != 0) {
    int128 minDepositRatePerSecondX18 = minDepositRateX18.div(...);
    int128 minDepositRateMultiplierX18 = (ONE + minDepositRatePerSecondX18).pow(int128(dt));

    state.cumulativeBorrowsMultiplierX18 = state.cumulativeBorrowsMultiplierX18
        .mul(minDepositRateMultiplierX18);

    state.cumulativeDepositsMultiplierX18 = state.cumulativeDepositsMultiplierX18
        .mul(minDepositRateMultiplierX18);
    ...
}
```

When `utilizationRatioX18 == 0` (depositors exist, no borrowers):
- `totalDepositRateX18 = 0`, `realizedDepositRateX18 = 0`, `feesAmt = 0` — the main path is a no-op.
- But `minDepositRateMultiplierX18 > ONE`, so `cumulativeDepositsMultiplierX18` grows.
- `totalBorrowsNormalized == 0`, so growing `cumulativeBorrowsMultiplierX18` affects no one.
- Net result: every depositor's real balance (`amountNormalized × cumulativeDepositsMultiplierX18`) increases with no borrower having paid for it.

The protocol's own invariant comment at lines 102–113 states that `(deposits - borrows)` must remain constant after a state update. This is violated: deposits grow, borrows stay at zero. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

Every depositor in a spot product configured with `minDepositRateX18 > 0` accrues interest during zero-utilization periods with no funding source. The `cumulativeDepositsMultiplierX18` is the multiplier used to convert normalized balances to real balances at withdrawal time:

```solidity
return Balance(balance.amountNormalized.mul(cumulativeMultiplierX18));
```

Inflating this multiplier during zero-utilization periods means withdrawing users receive more tokens than were ever deposited by borrowers. The shortfall must be absorbed by the protocol's own liquidity pool (NLP), constituting a direct, unbounded asset drain proportional to `minDepositRateX18 × totalDeposits × zero-utilization duration`. [3](#0-2) [4](#0-3) 

---

### Likelihood Explanation

Zero-utilization periods are routine: early in a product's lifecycle before any borrower opens a position, after all borrowers repay, or during market stress when borrowing demand collapses. Any spot product added via `addOrUpdateProduct` can be configured with a non-zero `minDepositRateX18`. The `updateStates` guard at line 277 only skips products with `totalDepositsNormalized == 0`; it does not skip products with `totalBorrowsNormalized == 0`. The sequencer submits `SpotTick` transactions continuously, so the multiplier grows on every tick during zero-utilization. [5](#0-4) [6](#0-5) 

---

### Recommendation

Mirror the zero-utilization guard already applied to `borrowerRateX18` for the `minDepositRateX18` block. When `utilizationRatioX18 == 0`, skip the `minDepositRateX18` application entirely, consistent with the documented invariant that an unused product's state should be time-invariant:

```solidity
if (minDepositRateX18 != 0 && utilizationRatioX18 != 0) {
    // apply the min deposit rate
    ...
}
```

This ensures the `minDepositRateX18` only accrues when there is active borrowing to fund it. [2](#0-1) 

---

### Proof of Concept

1. Admin calls `SpotEngine.addOrUpdateProduct` with `config.minDepositRateX18 = 1e16` (1% annualized).
2. Alice deposits 1,000,000 USDC into the product. `totalDepositsNormalized > 0`, `totalBorrowsNormalized == 0`.
3. No borrower opens a position. `utilizationRatioX18 == 0`.
4. Sequencer submits `SpotTick` transactions over 365 days. Each tick calls `spotEngine.updateStates(dt)` → `_updateState`.
5. In each `_updateState` call: `borrowerRateX18 = 0` (zero-utilization guard fires), `feesAmt = 0`. But `minDepositRateX18 != 0`, so `cumulativeDepositsMultiplierX18` compounds at 1%/year.
6. After 365 days, Alice's balance reads `1,000,000 × 1.01 = 1,010,000 USDC`.
7. Alice withdraws 1,010,000 USDC. The extra 10,000 USDC was never paid by any borrower — it is extracted from the NLP pool's liquidity. [7](#0-6) [2](#0-1) [5](#0-4)

### Citations

**File:** core/contracts/SpotEngineState.sol (L57-100)
```text
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

**File:** core/contracts/SpotEngineState.sol (L102-113)
```text
        // if we don't take fees into account, the liquidity, which is
        // (deposits - borrows) should remain the same after updating state.

        // For simplicity, we use `tb`, `cbm`, `td`, and `cdm` for
        // `totalBorrowsNormalized`, `cumulativeBorrowsMultiplier`,
        // `totalDepositsNormalized`, and `cumulativeDepositsMultiplier`

        // before the updating, the liquidity is (td * cdm - tb * cbm)
        // after the updating, the liquidity is
        // (td * cdm * depositRateMultiplier - tb * cbm * borrowRateMultiplier)
        // so we can get
        // depositRateMultiplier = utilization * (borrowRateMultiplier - 1) + 1
```

**File:** core/contracts/SpotEngineState.sol (L147-169)
```text
        // apply the min deposit rate
        if (minDepositRateX18 != 0) {
            int128 minDepositRatePerSecondX18 = minDepositRateX18.div(
                MathSD21x18.fromInt(31536000)
            );
            int128 minDepositRateMultiplierX18 = (ONE +
                minDepositRatePerSecondX18).pow(int128(dt));

            state.cumulativeBorrowsMultiplierX18 = state
                .cumulativeBorrowsMultiplierX18
                .mul(minDepositRateMultiplierX18);

            state.cumulativeDepositsMultiplierX18 = state
                .cumulativeDepositsMultiplierX18
                .mul(minDepositRateMultiplierX18);

            depositRateMultiplierX18 = depositRateMultiplierX18.mul(
                minDepositRateMultiplierX18
            );
            borrowRateMultiplierX18 = borrowRateMultiplierX18.mul(
                minDepositRateMultiplierX18
            );
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
