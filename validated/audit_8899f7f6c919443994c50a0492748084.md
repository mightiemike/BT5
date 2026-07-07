### Title
`minDepositRate` Inflates Depositor Balances Without Corresponding Token Backing When Utilization Is Zero — (`File: core/contracts/SpotEngineState.sol`)

---

### Summary

`SpotEngineState._updateState()` unconditionally applies `minDepositRateMultiplierX18` to `cumulativeDepositsMultiplierX18` even when `utilizationRatioX18 == 0` (no borrowers). This inflates all depositor balances beyond the actual token holdings of the Clearinghouse, creating phantom yield with no funding source, and enabling early withdrawers to drain funds that later withdrawers are owed.

---

### Finding Description

In `_updateState`, when `utilizationRatioX18 == 0`, the code explicitly zeroes out the borrow rate:

```solidity
if (utilizationRatioX18 == 0) {
    borrowerRateX18 = 0;
}
```

This means `borrowRateMultiplierX18 = 1`, `totalDepositRateX18 = 0`, and `realizedDepositRateX18 = 0`. No interest accrues from borrowers. The regular deposit multiplier stays at `1`.

However, the `minDepositRate` block runs unconditionally afterward:

```solidity
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
``` [1](#0-0) 

Since `totalBorrowsNormalized == 0`, growing `cumulativeBorrowsMultiplierX18` creates no real liability — there are no borrowers to owe more. But `totalDepositsNormalized > 0` (the early-return guard at line 277 ensures this), so growing `cumulativeDepositsMultiplierX18` directly inflates every depositor's redeemable balance:

```
depositorBalance = amountNormalized × cumulativeDepositsMultiplierX18
``` [2](#0-1) 

The actual ERC-20 token balance held by the Clearinghouse does not grow. No external yield source, insurance draw, or protocol subsidy is triggered. The protocol's internal accounting diverges from its real token holdings with every `updateStates` call.

The `assertUtilization` guard called on every withdrawal only checks `totalDeposits >= totalBorrows`:

```solidity
require(totalDeposits >= totalBorrows, ERR_MAX_UTILIZATION);
``` [3](#0-2) 

This invariant is trivially satisfied when `totalBorrows == 0`, so it never catches the insolvency. The withdrawal path in `Clearinghouse.withdrawCollateral` calls `assertUtilization` but has no check that the actual token balance covers `totalDeposits`. [4](#0-3) 

---

### Impact Explanation

**Impact: High.**

Every `updateStates` call (sequencer-driven, periodic) silently inflates `cumulativeDepositsMultiplierX18` for any spot product configured with `minDepositRateX18 > 0` and zero utilization. Depositors who withdraw early receive more tokens than they deposited. The shortfall is borne by later withdrawers, who find the Clearinghouse insolvent. The invariant `actualTokenBalance(clearinghouse) >= sum(depositorBalances)` is broken permanently and compounds over time.

---

### Likelihood Explanation

**Likelihood: Low-to-Medium.**

The condition requires: (1) a spot product configured with `minDepositRateX18 > 0`, and (2) zero borrowing utilization for that product. Both are realistic — a newly listed asset or a low-demand collateral token may have depositors but no borrowers for extended periods. The QUOTE product is initialized with `minDepositRateX18: 0`, so it is not affected, but any other spot product added via `addOrUpdateProduct` with a non-zero `minDepositRateX18` is vulnerable. [5](#0-4) 

---

### Recommendation

The `minDepositRate` subsidy must only be applied when there is a funded source. Two options:

1. **Skip `minDepositRate` when utilization is zero**: add `if (utilizationRatioX18 == 0) { minDepositRateX18 = 0; }` before the min-deposit-rate block, consistent with how `borrowerRateX18` is already zeroed.
2. **Fund the subsidy from insurance**: when utilization is zero, draw the equivalent `minDepositRate` yield from the protocol insurance fund rather than creating it from nothing.

---

### Proof of Concept

**Setup:** A spot product `P` is added with `minDepositRateX18 = 0.05e18` (5% annualized). No borrowers exist (`totalBorrowsNormalized = 0`).

**Step 1 — Alice deposits 1000 USDC.** `totalDepositsNormalized = 1000 / cumulativeDepositsMultiplierX18 = 1000`. Clearinghouse holds 1000 USDC.

**Step 2 — Sequencer calls `updateStates` after 1 year (`dt = 31536000`).** `utilizationRatioX18 = 0`, so `borrowerRateX18 = 0`, `totalDepositRateX18 = 0`. Regular multipliers unchanged. Then `minDepositRateMultiplierX18 ≈ 1.05`. `cumulativeDepositsMultiplierX18` becomes `1.05`. Alice's balance is now `1000 × 1.05 = 1050`. Clearinghouse still holds 1000 USDC.

**Step 3 — Alice calls `withdrawCollateral` for 1050 USDC.** `assertUtilization` passes (`totalDeposits = 1050 >= totalBorrows = 0`). Health check passes. The Clearinghouse attempts to transfer 1050 USDC but only holds 1000 — the transfer reverts or, if another depositor's funds are present, drains them instead.

**Step 4 — Bob (another depositor) attempts to withdraw his 1000 USDC.** The Clearinghouse is short by 50 USDC. Bob cannot fully withdraw. Protocol is insolvent. [6](#0-5) [1](#0-0)

### Citations

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

**File:** core/contracts/SpotEngine.sol (L23-31)
```text
        configs[QUOTE_PRODUCT_ID] = Config({
            token: _quote,
            interestInflectionUtilX18: 8e17, // .8
            interestFloorX18: 1e16, // .01
            interestSmallCapX18: 4e16, // .04
            interestLargeCapX18: ONE, // 1
            withdrawFeeX18: ONE, // 1
            minDepositRateX18: 0 // 0
        });
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
