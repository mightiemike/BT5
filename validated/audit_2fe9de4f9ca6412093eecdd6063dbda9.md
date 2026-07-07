### Title
Builder Fee Off-Balance-Sheet Accounting Understates `totalDepositsNormalized`, Corrupting Interest Rate Calculations — (`core/contracts/OffchainExchange.sol`)

---

### Summary

When a trade is executed with a builder fee, the builder fee is deducted from the user's quote balance (reducing `totalDepositsNormalized` in the spot engine) but is stored off-balance-sheet in the `collectedBuilderFee` mapping. The protocol fee is promptly credited back via `dumpFees`, but the builder fee is only credited back to the spot engine when the builder explicitly calls `claimBuilderFee`. During the entire period between trade and claim, `totalDepositsNormalized` is understated by the sum of all unclaimed builder fees, causing the utilization ratio and interest rates to be computed incorrectly.

---

### Finding Description

In `applyFee`, both the protocol fee and the builder fee are deducted from the user's `quoteDelta`:

```solidity
orderInfo.quoteDelta =
    orderInfo.quoteDelta -
    orderInfo.fee -
    orderInfo.builderFee;
``` [1](#0-0) 

The builder fee is stored in an off-balance-sheet mapping inside `OffchainExchange`:

```solidity
collectedBuilderFee[market.quoteId][feeInfo.builderId] += orderInfo.builderFee;
``` [2](#0-1) 

When `_updateBalances` is subsequently called, the user's spot engine balance decreases by `fee + builderFee`, which reduces `totalDepositsNormalized` in the spot engine for the quote token.

The **protocol fee** portion is credited back to the spot engine promptly by the sequencer via `dumpFees`:

```solidity
spotEngine.updateBalance(
    quoteIds[productId],
    X_ACCOUNT,
    market.collectedFees
);
``` [3](#0-2) 

The **builder fee** portion, however, is only credited back when the builder explicitly calls `claimBuilderFee`:

```solidity
spotEngine.updateBalance(productId, sender, collectedFee);
collectedBuilderFee[productId][builderId] = 0;
``` [4](#0-3) 

During the entire window between trade execution and builder claim, `totalDepositsNormalized` in the spot engine is understated by the sum of all unclaimed builder fees. The interest rate calculation in `SpotEngineState._updateState` directly uses this value:

```solidity
int128 utilizationRatioX18 = totalBorrows.div(totalDeposits);
``` [5](#0-4) 

With `totalDeposits` understated, `utilizationRatioX18` is overstated, which inflates `borrowerRateX18` and `depositRateMultiplierX18` beyond their correct values. [6](#0-5) 

This is the direct analog to the TermMax bug: in TermMax, the APR calculation uses the full fee as if it all returns to the pool, while only a partial fee does. In Nado, the interest rate calculation uses `totalDepositsNormalized` as if the full fee remains in the spot engine, while the builder fee portion is silently removed until claimed.

---

### Impact Explanation

- `totalDepositsNormalized` is persistently understated by the cumulative sum of all unclaimed builder fees across all active builders.
- The utilization ratio is overstated, causing `borrowerRateX18` to be computed higher than correct.
- All borrowers pay inflated interest; all depositors receive inflated interest (funded by the overstated borrow rate).
- The protocol fee (`INTEREST_FEE_FRACTION`) accrued to `FEES_ACCOUNT` is also inflated.
- The discrepancy is permanent for any builder who never claims, and grows monotonically with trading volume routed through builders. [7](#0-6) 

---

### Likelihood Explanation

Any trade routed through a builder with a non-zero `builderFeeRate` triggers this. The `builderFeeRate` is set by the builder and validated against `lowestFeeRate`/`highestFeeRate` in `getUserFeeRateWithBuilder`. [8](#0-7) 

Builders have no on-chain incentive to claim frequently. The longer the claim delay, the larger the accumulated understatement. This is reachable by any unprivileged trader placing an order through a registered builder — no special access is required.

---

### Recommendation

When builder fees are collected in `applyFee`, they should be immediately credited to a designated escrow account in the spot engine (e.g., a per-builder balance or a shared builder-fee reserve account), rather than stored in the off-balance-sheet `collectedBuilderFee` mapping. This ensures `totalDepositsNormalized` remains accurate at all times. When `claimBuilderFee` is called, the balance should be transferred from the escrow account to the builder's account (a balance-neutral operation), rather than creating new balance via `spotEngine.updateBalance`.

---

### Proof of Concept

1. Builder registers with `builderFeeRate = 10 bps`.
2. User A places a taker order routed through the builder; `builderFee = 100 USDC` is deducted from `quoteDelta`.
3. `collectedBuilderFee[quoteId][builderId] += 100` — stored off-balance-sheet.
4. `_updateBalances` runs: User A's spot balance decreases by 100 USDC → `totalDepositsNormalized` decreases by 100 USDC.
5. `dumpFees` is called by the sequencer: protocol fee is credited to `X_ACCOUNT` → `totalDepositsNormalized` recovers the protocol fee portion only.
6. Builder does not call `claimBuilderFee` for an extended period.
7. `updateStates` is called: `utilizationRatioX18 = totalBorrows / (totalDeposits - 100)` — overstated.
8. `borrowerRateX18` is computed higher than correct; `cumulativeBorrowsMultiplierX18` and `cumulativeDepositsMultiplierX18` are inflated.
9. All borrowers accrue excess interest; all depositors receive excess interest; the protocol fee is inflated.
10. When the builder finally calls `claimBuilderFee`, `spotEngine.updateBalance(quoteId, builder, 100)` restores `totalDepositsNormalized` — but all interest computed during the gap was already incorrect. [9](#0-8) [10](#0-9)

### Citations

**File:** core/contracts/OffchainExchange.sol (L483-506)
```text
        (uint32 builderId, int128 builderFeeRate) = _builderInfo(appendix);
        Builder memory builder;
        if (builderId != 0) {
            builder = builders[builderId];
            if (
                builder.owner == address(0) ||
                builderFeeRate > builder.highestFeeRate ||
                builderFeeRate < builder.lowestFeeRate
            ) {
                revert(ERR_INVALID_BUILDER);
            }
        } else if (builderFeeRate != 0) {
            revert(ERR_INVALID_BUILDER);
        }

        uint32 feeTier = feeTiers[address(uint160(bytes20(sender)))];
        if (feeTier < builder.defaultFeeTier) {
            feeTier = builder.defaultFeeTier;
        }
        FeeRates memory userFeeRates = getTierFeeRateX18(feeTier, productId);
        int128 feeRate = taker
            ? userFeeRates.takerRateX18
            : userFeeRates.makerRateX18;
        return FeeInfo(feeRate, builderId, builderFeeRate);
```

**File:** core/contracts/OffchainExchange.sol (L562-565)
```text
        orderInfo.quoteDelta =
            orderInfo.quoteDelta -
            orderInfo.fee -
            orderInfo.builderFee;
```

**File:** core/contracts/OffchainExchange.sol (L566-569)
```text
        if (orderInfo.builderFee > 0) {
            collectedBuilderFee[market.quoteId][feeInfo.builderId] += orderInfo
                .builderFee;
            emitBuilderEvent(orderInfo, feeInfo.builderId, productId);
```

**File:** core/contracts/OffchainExchange.sol (L869-889)
```text
    function claimBuilderFee(bytes32 sender, uint32 builderId)
        external
        onlyEndpoint
    {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(
            builders[builderId].owner == address(uint160(bytes20(sender))),
            ERR_UNAUTHORIZED
        );
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint32 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            int128 collectedFee = collectedBuilderFee[productId][builderId];
            if (collectedFee == 0) {
                continue;
            }
            emit ClaimBuilderFee(builderId, productId, sender, collectedFee);
            spotEngine.updateBalance(productId, sender, collectedFee);
            collectedBuilderFee[productId][builderId] = 0;
        }
    }
```

**File:** core/contracts/OffchainExchange.sol (L902-906)
```text
            spotEngine.updateBalance(
                quoteIds[productId],
                X_ACCOUNT,
                market.collectedFees
            );
```

**File:** core/contracts/SpotEngineState.sol (L61-64)
```text
        int128 totalBorrows = state.totalBorrowsNormalized.mul(
            state.cumulativeBorrowsMultiplierX18
        );
        int128 utilizationRatioX18 = totalBorrows.div(totalDeposits);
```

**File:** core/contracts/SpotEngineState.sol (L70-98)
```text
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

**File:** core/contracts/SpotEngineState.sol (L115-127)
```text
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
