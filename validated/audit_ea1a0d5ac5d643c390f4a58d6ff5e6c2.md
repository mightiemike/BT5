### Title
Unconditional `minDepositRateX18` Application Creates Unbacked Depositor Yield, Breaking Solvency Invariant — (`core/contracts/SpotEngineState.sol`)

---

### Summary

In `SpotEngineState._updateState`, `minDepositRateX18` is applied as an unconditional additive multiplier to **both** `cumulativeBorrowsMultiplierX18` and `cumulativeDepositsMultiplierX18` on every state update, regardless of utilization. This causes the protocol's total deposit obligations to grow faster than its actual token holdings when utilization is below 100%, creating an unfunded deficit proportional to `(totalDeposits − totalBorrows) × (minDepositMultiplier − 1)` per update. The `assertUtilization` check does not catch this because it only verifies `totalDeposits ≥ totalBorrows` in normalized terms, which remains true even as the absolute deficit grows.

---

### Finding Description

In `_updateState` (lines 147–169 of `SpotEngineState.sol`), after computing the regular borrow and deposit rate multipliers, the code unconditionally applies `minDepositRateMultiplierX18` to both cumulative multipliers:

```solidity
// apply the min deposit rate
if (minDepositRateX18 != 0) {
    int128 minDepositRatePerSecondX18 = minDepositRateX18.div(
        MathSD21x18.fromInt(31536000)
    );
    int128 minDepositRateMultiplierX18 = (ONE +
        minDepositRatePerSecondX18).pow(int128(dt));

    state.cumulativeBorrowsMultiplierX18 = state
        .cumulativeBorrowsMultiplierX18
        .mul(minDepositRateMultiplierX18);   // borrowers pay extra

    state.cumulativeDepositsMultiplierX18 = state
        .cumulativeDepositsMultiplierX18
        .mul(minDepositRateMultiplierX18);   // depositors earn extra
    ...
}
```

The code comment says "apply the min deposit rate," implying it should act as a **floor** for depositors. Instead, it acts as an **additive surcharge** applied on top of the regular rates every single update, regardless of whether the regular deposit rate already exceeds the minimum.

The liquidity invariant the protocol relies on (documented in the comment at lines 102–113) is:

```
L = totalDeposits × depositMultiplier − totalBorrows × borrowMultiplier = constant
```

After applying the regular rates, `L` is preserved. But after applying `minDepositRateMultiplierX18` to both multipliers:

```
L' = L × minDepositRateMultiplierX18
```

The liquidity grows by `minDepositRateMultiplierX18` on every call to `updateStates`. This growth is **unbacked** — no new tokens enter the contract. The protocol's actual token balance is `L` (deposits minus borrows in real tokens), but after the update it claims to owe depositors `L × minDepositRateMultiplierX18`. The unfunded deficit per update is:

```
deficit = L × (minDepositRateMultiplierX18 − 1)
        = (totalDeposits − totalBorrows) × (minDepositRateMultiplierX18 − 1)
```

This deficit is worst when utilization is zero (no borrowers at all), because `L = totalDeposits` and the entire depositor yield is unfunded.

The `assertUtilization` check in `SpotEngine.sol` (line 240) does not catch this:

```solidity
require(totalDeposits >= totalBorrows, ERR_MAX_UTILIZATION);
```

Since `minDepositRate` scales both multipliers by the same factor, the ratio `totalDeposits / totalBorrows` is unchanged, so this check always passes even as the absolute deficit compounds over time.

The analog to the reported `minLiquidityRate` bug is direct: the report identifies that the constraint `A0 ≤ A2` is not enforced at `t = 0` (zero-utilization case, where `f(0) ≈ rB − rL`). In Nado, the analogous constraint — that depositor yield must be funded by borrower payments — is not enforced when utilization is zero or low, because `minDepositRateX18` is applied unconditionally rather than only when the regular deposit rate falls below the minimum.

---

### Impact Explanation

Any product configured with `minDepositRateX18 > 0` and sub-100% utilization accumulates an unfunded deficit on every `updateStates` call. A depositor who deposits into such a product and later withdraws receives more tokens than were deposited, with the excess drawn from the protocol's insurance fund or other depositors' capital. At zero utilization, the entire `minDepositRate` yield is unfunded. At scale, this drains the protocol's solvency.

Concrete asset delta: a depositor of `D` tokens into a product with `minDepositRateX18 = r` and zero utilization, after `T` seconds, can withdraw `D × (1 + r/31536000)^T` tokens while the contract only holds `D`. The excess `D × ((1 + r/31536000)^T − 1)` is extracted from the protocol's reserves.

---

### Likelihood Explanation

`minDepositRateX18` is a configurable parameter set by the owner via `addOrUpdateProduct`. The QUOTE product is initialized with `minDepositRateX18: 0`, but any non-quote product can be configured with a nonzero value. Once any such product exists and has depositors, the deficit accrues automatically on every `updateStates` call (which is called by the Endpoint on a regular schedule). No special attacker action is needed beyond depositing and waiting — the protocol itself calls `updateStates`.

---

### Recommendation

Replace the unconditional application of `minDepositRateX18` with a conditional floor: only apply `minDepositRateMultiplierX18` to `cumulativeDepositsMultiplierX18` when the computed `depositRateMultiplierX18` falls below `minDepositRateMultiplierX18`, and in that case charge the shortfall to borrowers (or to the insurance fund) rather than creating it out of thin air. Specifically:

```solidity
if (minDepositRateX18 != 0) {
    int128 minDepositRatePerSecondX18 = minDepositRateX18.div(
        MathSD21x18.fromInt(31536000)
    );
    int128 minDepositRateMultiplierX18 = (ONE +
        minDepositRatePerSecondX18).pow(int128(dt));

    // Only apply if regular deposit rate is below the minimum
    if (depositRateMultiplierX18 < minDepositRateMultiplierX18) {
        int128 shortfallMultiplier = minDepositRateMultiplierX18.div(
            depositRateMultiplierX18
        );
        // Apply shortfall to both to preserve the funding relationship,
        // or charge only to borrowers and fund from insurance
        state.cumulativeBorrowsMultiplierX18 = state
            .cumulativeBorrowsMultiplierX18
            .mul(shortfallMultiplier);
        state.cumulativeDepositsMultiplierX18 = state
            .cumulativeDepositsMultiplierX18
            .mul(shortfallMultiplier);
    }
}
```

Additionally, validate at product configuration time that `minDepositRateX18` does not exceed the minimum possible borrow rate (i.e., `interestFloorX18`), analogous to the `fmax ≤ r_MP_L` constraint described in the referenced report.

---

### Proof of Concept

1. Owner configures a spot product with `minDepositRateX18 = 1e17` (10% annualized) via `addOrUpdateProduct`.
2. Alice deposits `1000e18` tokens into this product. No borrowers exist (utilization = 0).
3. The Endpoint calls `updateStates(dt)` with `dt = 31536000` (one year).
4. In `_updateState`: `utilizationRatioX18 = 0`, so `borrowerRateX18 = 0`, `borrowRateMultiplierX18 = ONE`, `realizedDepositRateX18 = 0`, `depositRateMultiplierX18 = ONE`.
5. Then `minDepositRateMultiplierX18 = (1 + 1e17/31536000)^31536000 ≈ 1.105e18` (≈ 10.5% compounded).
6. Both multipliers are scaled by `1.105e18`. Alice's balance is now `1105e18`.
7. Alice calls `withdrawCollateral` for `1105e18` tokens. `assertUtilization` passes (totalDeposits ≥ totalBorrows = 0). Health check passes.
8. The contract transfers `1105e18` tokens to Alice, but only held `1000e18`. The `105e18` excess is drawn from other protocol reserves or causes a revert if the contract balance is exactly `1000e18`, demonstrating the insolvency. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** core/contracts/SpotEngineState.sol (L64-99)
```text
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
