### Title
Protocol Fee Balance Inflates `totalDeposits` Denominator, Causing Systematic Understatement of Utilization Rate and Borrowing Rates — (`File: core/contracts/SpotEngineState.sol`)

---

### Summary

In `SpotEngineState._updateState`, the utilization ratio is computed as `totalBorrows / totalDeposits`. However, `totalDepositsNormalized` — and therefore `totalDeposits` — includes the `FEES_ACCOUNT` balance, which represents protocol-owned interest fees that are not available for borrowing. As fees compound into `FEES_ACCOUNT` with every interest accrual cycle, the denominator is progressively inflated, causing the utilization ratio to be systematically understated. This leads to interest rates that are lower than warranted, causing depositors to earn less than they should. The same inflated `totalDeposits` is used in `assertUtilization`, making the solvency guard less strict than intended.

---

### Finding Description

In `_updateState` (`SpotEngineState.sol`, line 64):

```solidity
int128 utilizationRatioX18 = totalBorrows.div(totalDeposits);
```

`totalDeposits` is computed as:

```solidity
int128 totalDeposits = state.totalDepositsNormalized.mul(
    state.cumulativeDepositsMultiplierX18
);
```

`totalDepositsNormalized` is the sum of **all** deposit-side normalized balances, including `FEES_ACCOUNT` (`bytes32(0)`). At the end of every `_updateState` call, protocol fees are credited directly into `FEES_ACCOUNT` as a deposit balance:

```solidity
_updateBalanceNormalized(state, feesAccBalance, feesAmt);
_setBalanceAndUpdateBitmap(productId, FEES_ACCOUNT, feesAccBalance);
```

Inside `_updateBalanceNormalized`, because `feesAmt > 0`, the new `FEES_ACCOUNT.amountNormalized` is positive, so it is added to `state.totalDepositsNormalized` (lines 45–46). This means every interest accrual cycle permanently inflates `totalDepositsNormalized` by the fee share.

The `FEES_ACCOUNT` balance represents protocol-owned fees that are **not** available for borrowing by users. Including them in the denominator of the utilization ratio is semantically incorrect: the true utilization should be `totalBorrows / (totalDeposits - feesBalance)`.

The same inflated `totalDeposits` is used in `assertUtilization` (`SpotEngine.sol`, lines 234–240):

```solidity
int128 totalDeposits = _state.totalDepositsNormalized.mul(
    _state.cumulativeDepositsMultiplierX18
);
int128 totalBorrows = _state.totalBorrowsNormalized.mul(
    _state.cumulativeBorrowsMultiplierX18
);
require(totalDeposits >= totalBorrows, ERR_MAX_UTILIZATION);
```

This guard is called on every withdrawal. Because `totalDeposits` is inflated by accumulated fees, the guard permits withdrawals that would leave actual user-deposited liquidity below total borrows.

---

### Impact Explanation

**Accounting corruption — interest rate understatement:**

With `INTEREST_FEE_FRACTION = 20%`, 20% of all interest paid by borrowers is credited to `FEES_ACCOUNT` as a deposit. Over time, this balance compounds (it earns the deposit multiplier like any other depositor), causing `totalDepositsNormalized` to grow beyond the sum of actual user deposits. The utilization ratio `totalBorrows / totalDeposits` is therefore smaller than the true ratio, driving the computed `borrowerRateX18` down the interest curve. Borrowers pay less than the protocol intends; depositors earn less than they are owed.

**Solvency guard weakened:**

`assertUtilization` is the only on-chain check preventing withdrawals that would leave the protocol unable to cover borrows. Because it uses the same inflated `totalDeposits`, it allows the sum of user withdrawals to exceed the sum of actual user deposits minus borrows. If the sequencer subsequently claims and withdraws accumulated fees via `claimSequencerFees`, the remaining user-deposited liquidity can fall below total borrows, creating an insolvency gap that is invisible to the guard.

---

### Likelihood Explanation

Fees accrue on every call to `updateStates`, which is triggered by the sequencer on a regular cadence (the endpoint enforces `dt < 7 * SECONDS_PER_DAY`). No special attacker action is required — the inflation is automatic and continuous. With `INTEREST_FEE_FRACTION = 20%` and realistic utilization (e.g., 50–80%), the fee balance grows to a non-trivial fraction of total deposits within months, making the understatement material. Any borrower benefits passively from the lower rates; any depositor is harmed.

---

### Recommendation

Exclude the `FEES_ACCOUNT` balance from the utilization ratio denominator. Compute the true borrowable liquidity as:

```solidity
int128 feesBalance = balanceNormalizedToBalance(
    state,
    balances[productId][FEES_ACCOUNT]
).amount;
int128 borrowableDeposits = totalDeposits - feesBalance;
int128 utilizationRatioX18 = totalBorrows.div(borrowableDeposits);
```

Apply the same correction in `assertUtilization` so the solvency guard reflects actual user-deposited liquidity.

---

### Proof of Concept

1. Protocol starts: `totalDepositsNormalized = 1000`, `totalBorrowsNormalized = 600`, `FEES_ACCOUNT.amountNormalized = 0`.
2. `_updateState` is called. `utilizationRatioX18 = 600/1000 = 60%`. Correct.
3. `feesAmt` is computed and credited to `FEES_ACCOUNT`. Suppose `feesAmt = 10`. Now `totalDepositsNormalized = 1010`.
4. Next `_updateState` call: `totalDeposits = 1010 * multiplier`. `utilizationRatioX18 = 600_adjusted / 1010_adjusted`. The denominator is inflated by the 10-unit fee balance. Utilization is understated.
5. After 1 year of operation at 50% utilization and 10% APR, fees ≈ 20% × 10% × deposits = 2% of deposits. Utilization is understated by ~2 percentage points, pushing the protocol below the inflection point on the interest curve and reducing borrower rates by a compounding margin.
6. `assertUtilization` passes for a withdrawal that leaves user deposits < borrows, because the fee balance pads `totalDeposits` past the threshold.

**Exact root cause lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/SpotEngineState.sol (L45-49)
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

**File:** core/contracts/SpotEngineState.sol (L139-145)
```text
        if (feesAmt != 0) {
            BalanceNormalized memory feesAccBalance = balances[productId][
                FEES_ACCOUNT
            ];
            _updateBalanceNormalized(state, feesAccBalance, feesAmt);
            _setBalanceAndUpdateBitmap(productId, FEES_ACCOUNT, feesAccBalance);
        }
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

**File:** core/contracts/common/Constants.sol (L8-8)
```text
bytes32 constant FEES_ACCOUNT = bytes32(0);
```

**File:** core/contracts/common/Constants.sol (L38-38)
```text
int128 constant INTEREST_FEE_FRACTION = 200_000_000_000_000_000; // 20%
```
