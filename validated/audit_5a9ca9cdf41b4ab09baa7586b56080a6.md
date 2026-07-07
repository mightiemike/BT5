### Title
FEES_ACCOUNT Phantom Balance Earns Deposit Interest, Continuously Diluting Real Depositor Yields — (File: `core/contracts/SpotEngineState.sol`)

---

### Summary

In `SpotEngineState._updateState`, protocol interest fees are credited to `FEES_ACCOUNT` (`bytes32(0)`) as a deposit balance via `_updateBalanceNormalized`. Because this function unconditionally adds the fee amount to `state.totalDepositsNormalized`, the `FEES_ACCOUNT` phantom balance participates in every subsequent deposit-rate calculation. This causes real depositors to earn a continuously shrinking share of borrower interest, with the difference silently diverted to the unowned `FEES_ACCOUNT`.

---

### Finding Description

`_updateState` in `SpotEngineState.sol` computes the deposit rate as:

```
utilizationRatioX18 = totalBorrows / totalDeposits
realizedDepositRateX18 = utilizationRatioX18 * (borrowRateMultiplier - 1) * (1 - INTEREST_FEE_FRACTION)
``` [1](#0-0) 

The fee amount is then credited to `FEES_ACCOUNT`: [2](#0-1) 

Inside `_updateBalanceNormalized`, the FEES_ACCOUNT's new normalized balance is unconditionally added to `state.totalDepositsNormalized`: [3](#0-2) 

From the very next call to `_updateState`, `totalDeposits` includes the accumulated `FEES_ACCOUNT` balance `F`. The utilization ratio becomes `B / (D + F)` instead of `B / D`, where `D` is real deposits and `B` is borrows. The deposit rate is therefore lower, and real depositors earn:

```
D * (B / (D + F)) * borrowRate * (1 - INTEREST_FEE_FRACTION)
```

instead of the correct:

```
D * (B / D) * borrowRate * (1 - INTEREST_FEE_FRACTION)
```

The difference — `D * B * borrowRate * (1 - fee) * F / (D * (D + F))` — is earned by `FEES_ACCOUNT` as deposit interest. `FEES_ACCOUNT` is `bytes32(0)`, a protocol-internal phantom account with no real depositor behind it. [4](#0-3) 

The `INTEREST_FEE_FRACTION` is 20%, so the protocol already takes 20% of borrower interest. The additional deposit interest earned by `FEES_ACCOUNT` means the protocol silently extracts **more than 20%** of borrower interest from real depositors, with the excess growing compoundingly as `F` accumulates. [5](#0-4) 

---

### Impact Explanation

Real depositors receive a continuously decreasing fraction of the interest they are owed. The loss is proportional to `F / (D + F)` per period, where `F` is the accumulated `FEES_ACCOUNT` balance. This fraction compounds: as `F` grows, the deposit rate dilution worsens, which causes `F` to grow faster. Over a protocol lifetime with sustained borrowing activity, the effective fee extraction can materially exceed the intended 20%, representing a direct, ongoing loss of yield for all spot depositors across every product.

---

### Likelihood Explanation

No attacker action is required. The loss occurs automatically on every call to `updateStates`, which is a routine sequencer-driven operation. The effect begins immediately after the first interest accrual and grows monotonically with protocol usage. Any user who deposits into a spot product and holds over time is affected. [6](#0-5) 

---

### Recommendation

When crediting `feesAmt` to `FEES_ACCOUNT`, do not route it through `_updateBalanceNormalized` in a way that inflates `totalDepositsNormalized`. Options:

1. Track the `FEES_ACCOUNT` balance in a separate storage variable outside the deposit pool, so it does not affect the utilization ratio or deposit rate.
2. Subtract the `FEES_ACCOUNT` normalized balance from `totalDepositsNormalized` before computing `utilizationRatioX18`, then add it back after the rate update.
3. Credit fees directly to an off-engine insurance or treasury variable (analogous to how `insurance` is tracked in `Clearinghouse.sol`) rather than as a deposit balance in the engine.

---

### Proof of Concept

```
Setup:
  - D = 1,000e18 USDC deposited by real users
  - B = 800e18 USDC borrowed (80% utilization)
  - borrowRate = 5% annualized, INTEREST_FEE_FRACTION = 20%

Period 1 (updateStates called):
  - utilizationRatio = 800 / 1000 = 0.80
  - feesAmt ≈ 1000 * 0.80 * 0.05/yr * 0.20 = 8 USDC/yr credited to FEES_ACCOUNT
  - realDepositors earn: 1000 * 0.80 * 0.05/yr * 0.80 = 32 USDC/yr ✓

Period 2 (FEES_ACCOUNT balance F = 8):
  - totalDeposits = 1000 + 8 = 1008
  - utilizationRatio = 800 / 1008 = 0.7937 (diluted)
  - realDepositors earn: 1000 * (800/1008) * 0.05/yr * 0.80 = 31.75 USDC/yr
  - FEES_ACCOUNT earns: 8 * (800/1008) * 0.05/yr * 0.80 = 0.254 USDC/yr (stolen)
  - Expected real depositor yield: 32 USDC/yr
  - Actual real depositor yield: 31.75 USDC/yr
  - Loss: 0.254 USDC/yr, growing each period as F compounds
```

The entry path is: sequencer calls `Endpoint` → `EndpointTx` → `SpotEngine.updateStates` → `SpotEngineState._updateState` → `_updateBalanceNormalized(state, feesAccBalance, feesAmt)`. No privileged access is required; the loss is structural and automatic. [7](#0-6)

### Citations

**File:** core/contracts/SpotEngineState.sol (L44-49)
```text

        if (balance.amountNormalized > 0) {
            state.totalDepositsNormalized += balance.amountNormalized;
        } else {
            state.totalBorrowsNormalized -= balance.amountNormalized;
        }
```

**File:** core/contracts/SpotEngineState.sol (L58-64)
```text
        int128 totalDeposits = state.totalDepositsNormalized.mul(
            state.cumulativeDepositsMultiplierX18
        );
        int128 totalBorrows = state.totalBorrowsNormalized.mul(
            state.cumulativeBorrowsMultiplierX18
        );
        int128 utilizationRatioX18 = totalBorrows.div(totalDeposits);
```

**File:** core/contracts/SpotEngineState.sol (L115-145)
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

        state.cumulativeBorrowsMultiplierX18 = state
            .cumulativeBorrowsMultiplierX18
            .mul(borrowRateMultiplierX18);

        int128 depositRateMultiplierX18 = ONE + realizedDepositRateX18;

        state.cumulativeDepositsMultiplierX18 = state
            .cumulativeDepositsMultiplierX18
            .mul(depositRateMultiplierX18);

        if (feesAmt != 0) {
            BalanceNormalized memory feesAccBalance = balances[productId][
                FEES_ACCOUNT
            ];
            _updateBalanceNormalized(state, feesAccBalance, feesAmt);
            _setBalanceAndUpdateBitmap(productId, FEES_ACCOUNT, feesAccBalance);
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

**File:** core/contracts/common/Constants.sol (L7-8)
```text
/// @dev Fees account
bytes32 constant FEES_ACCOUNT = bytes32(0);
```

**File:** core/contracts/common/Constants.sol (L38-38)
```text
int128 constant INTEREST_FEE_FRACTION = 200_000_000_000_000_000; // 20%
```
