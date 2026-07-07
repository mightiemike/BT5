### Title
SpotEngine `minDepositRateX18` Accrues Phantom Interest Unbacked by Real Tokens, Causing Accounting-to-Token Desynchronization — (File: `core/contracts/SpotEngineState.sol`)

---

### Summary

When a spot product is configured with `minDepositRateX18 > 0` and utilization is zero (or low), `SpotEngineState._updateState` inflates `cumulativeDepositsMultiplierX18` without any corresponding increase in actual ERC-20 token holdings. The protocol's internal accounting diverges from the real token balance held by the `Clearinghouse`, and there is no sync mechanism to reconcile them. This is a direct analog to the wibBTC/Curve desynchronization: the pool's recorded balance drifts away from the actual balance, and the protocol has no way to detect or correct it.

---

### Finding Description

In `_updateState`, after computing normal borrow-funded interest, the `minDepositRateX18` path unconditionally increases both multipliers:

```solidity
// SpotEngineState.sol lines 155–168
state.cumulativeBorrowsMultiplierX18 = state
    .cumulativeBorrowsMultiplierX18
    .mul(minDepositRateMultiplierX18);

state.cumulativeDepositsMultiplierX18 = state
    .cumulativeDepositsMultiplierX18
    .mul(minDepositRateMultiplierX18);
```

When `totalBorrowsNormalized == 0` (no borrowers), multiplying `cumulativeBorrowsMultiplierX18` has no real effect — `0 × anything = 0` debt. But multiplying `cumulativeDepositsMultiplierX18` inflates every depositor's accounting balance without any token inflow to the `Clearinghouse`. The protocol creates interest credits that are not backed by real tokens.

The only guard on withdrawal is `assertUtilization`:

```solidity
// SpotEngineState.sol lines 232–241
function assertUtilization(uint32 productId) external view {
    ...
    int128 totalDeposits = _state.totalDepositsNormalized.mul(
        _state.cumulativeDepositsMultiplierX18
    );
    int128 totalBorrows = _state.totalBorrowsNormalized.mul(
        _state.cumulativeBorrowsMultiplierX18
    );
    require(totalDeposits >= totalBorrows, ERR_MAX_UTILIZATION);
}
```

This check only compares accounting values against each other. It does not compare `totalDeposits` against the actual ERC-20 balance of the `Clearinghouse`. With phantom interest and zero utilization, `totalDeposits > 0` and `totalBorrows = 0`, so the check always passes — even when the accounting balance exceeds the real token balance.

The `_balanceOf` helper exists in `Clearinghouse` but is never used in withdrawal validation:

```solidity
// Clearinghouse.sol line 387–389
function _balanceOf(address token) internal view virtual returns (uint128) {
    return uint128(IERC20Base(token).balanceOf(address(this)));
}
```

There is no `sync()` equivalent — no mechanism to reconcile the accounting with actual holdings.

The desynchronization also occurs at partial utilization. If `minDepositRateX18 = 5%` and the borrow-funded deposit rate is only `0.5%`, the net phantom interest is `4.5%` per year — entirely unfunded.

---

### Impact Explanation

The `Clearinghouse` holds fewer real tokens than the sum of all depositor accounting balances. The first depositors to withdraw receive their full credited amount (including phantom interest). Later depositors find the contract underfunded and their withdrawals revert at the `safeTransfer` call inside `handleWithdrawTransfer`. This is a solvency/accounting corruption: the protocol becomes insolvent relative to its obligations whenever `minDepositRateX18 > 0` and utilization is below the break-even point.

---

### Likelihood Explanation

`minDepositRateX18` is a configurable parameter set per product via `addOrUpdateProduct`. The quote product initializes it to `0`, but any non-quote spot product can be configured with a non-zero value. Once set, the desynchronization accrues automatically on every `updateStates` call driven by the sequencer — no further privileged action is required. Low-utilization periods (e.g., at product launch, during market downturns) are common and predictable.

---

### Recommendation

The `minDepositRateX18` subsidy must be funded from an explicit source when borrower interest is insufficient. Options include:

1. **Cap the deposit rate at the borrow-funded rate**: Do not apply `minDepositRateX18` when `utilizationRatioX18 == 0`, or only apply it up to the amount funded by borrowers.
2. **Fund from insurance**: Deduct the unfunded portion of `minDepositRateX18` from the `insurance` balance in `Clearinghouse`, reverting if insurance is insufficient.
3. **Add a real-balance solvency check**: In `assertUtilization`, additionally require `totalDeposits <= actualERC20Balance + totalBorrows` to detect and block withdrawals that would drain the contract below its obligations.

---

### Proof of Concept

**Setup:**
- A non-quote spot product is configured with `minDepositRateX18 = 5e16` (5% annualized).
- Alice deposits 100 tokens. `totalDepositsNormalized = 100 / ONE = 100`, `cumulativeDepositsMultiplierX18 = ONE`.
- No borrowers exist: `totalBorrowsNormalized = 0`.

**After 1 year of sequencer-driven `updateStates` calls:**
- `utilizationRatioX18 = 0`, so `borrowerRateX18 = 0`, `realizedDepositRateX18 = 0`.
- `minDepositRateMultiplierX18 ≈ 1.05`.
- `cumulativeDepositsMultiplierX18 ≈ 1.05 * ONE`.
- Alice's accounting balance: `100 * 1.05 = 105` tokens.
- Actual ERC-20 balance of `Clearinghouse`: **100 tokens** (unchanged).

**Withdrawal attempt:**
- Alice calls `withdrawCollateral` for 105 tokens.
- `spotEngine.updateBalance(productId, alice, -105e18)` succeeds (accounting allows it).
- `spotEngine.assertUtilization(productId)`: `totalDeposits = 0 >= totalBorrows = 0` → passes.
- `handleWithdrawTransfer` calls `token.safeTransfer(withdrawPool, 105)`.
- `Clearinghouse` only holds 100 tokens → **transfer reverts**.

Alice cannot withdraw her full credited balance. If Bob also deposited 100 tokens (total 200 deposited, 210 credited), Alice withdrawing 105 first leaves only 95 tokens for Bob, who is credited 105 — a 10-token shortfall with no recovery path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** core/contracts/SpotEngineState.sol (L115-137)
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
```

**File:** core/contracts/SpotEngineState.sol (L147-168)
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
```

**File:** core/contracts/SpotEngineState.sol (L232-241)
```text

    // TODO: maybe combine the next two functions
    // probably also need some protection where quote state must
    // be fetched through getQuoteState
    function getStateAndBalance(uint32 productId, bytes32 subaccount)
        public
        view
        returns (State memory, Balance memory)
    {
        State memory state = states[productId];
```

**File:** core/contracts/Clearinghouse.sol (L387-389)
```text
    function _balanceOf(address token) internal view virtual returns (uint128) {
        return uint128(IERC20Base(token).balanceOf(address(this)));
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
