### Title
Division by Zero in `_updateState` When `interestInflectionUtilX18 == ONE` Freezes Protocol-Wide Interest Accrual — (File: `core/contracts/SpotEngineState.sol`)

---

### Summary

When a spot product is configured with `interestInflectionUtilX18 == ONE` (inflection at 100% utilization), the `_updateState` function reverts with a division-by-zero error the moment utilization reaches or exceeds 100%. Because `updateStates` iterates over all products in a single call, this revert freezes interest accrual for every product in the protocol.

---

### Finding Description

`SpotEngineState._updateState` implements a two-segment piecewise interest rate model. The high-utilization branch computes:

```solidity
borrowerRateX18 +=
    config.interestSmallCapX18 +
    config.interestLargeCapX18.mul(
        (
            (utilizationRatioX18 -
                config.interestInflectionUtilX18).div(
                    ONE - config.interestInflectionUtilX18   // ← denominator
                )
        )
    );
``` [1](#0-0) 

The denominator is `ONE - config.interestInflectionUtilX18`. When `interestInflectionUtilX18 == ONE` (a valid configuration meaning "apply the small-cap rate for all utilization below 100%, and the large-cap rate above"), this denominator is exactly zero.

The branch is entered whenever `utilizationRatioX18 >= config.interestInflectionUtilX18`:

```solidity
} else if (utilizationRatioX18 < config.interestInflectionUtilX18) {
    ...
} else {
    // entered when utilizationRatioX18 >= ONE → denominator is 0
``` [2](#0-1) 

`MathSD21x18.div` explicitly reverts on a zero denominator:

```solidity
function div(int128 x, int128 y) internal pure returns (int128) {
    unchecked {
        require(y != 0, ERR_DIV_BY_ZERO);
``` [3](#0-2) 

`addOrUpdateProduct` in `SpotEngine` accepts the `Config` struct without validating that `interestInflectionUtilX18 < ONE`, so the configuration is silently accepted:

```solidity
function addOrUpdateProduct(..., Config calldata config, ...) public onlyOwner {
    ...
    configs[productId] = config;
``` [4](#0-3) 

`updateStates` loops over all products and calls `_updateState` for each. A revert inside any single product's `_updateState` propagates and aborts the entire call:

```solidity
function updateStates(uint128 dt) external onlyEndpoint {
    ...
    for (uint32 i = 0; i < productIds.length; i++) {
        ...
        _updateState(productId, state, dt);
``` [5](#0-4) 

---

### Impact Explanation

**Impact: High.** When `updateStates` reverts, interest accrual is frozen for every product in the protocol — not just the misconfigured one. Borrowers receive free loans indefinitely; depositors lose all interest income. The protocol's core interest model is inoperative until the configuration is corrected by the owner.

---

### Likelihood Explanation

**Likelihood: Low.** Two conditions must coincide:
1. The owner sets `interestInflectionUtilX18 == ONE` for a product (a legitimate edge-case configuration, not validated by the contract).
2. Utilization for that product reaches or exceeds 100% (`totalBorrows >= totalDeposits`), which is a realistic market condition under high demand.

---

### Recommendation

Add a guard in `_updateState` for the degenerate case, mirroring the fix pattern from the external report:

```solidity
} else {
    int128 denom = ONE - config.interestInflectionUtilX18;
    if (denom == 0) {
        // inflection at 100%: large-cap rate applies fully above inflection
        borrowerRateX18 += config.interestSmallCapX18 + config.interestLargeCapX18;
    } else {
        borrowerRateX18 +=
            config.interestSmallCapX18 +
            config.interestLargeCapX18.mul(
                (utilizationRatioX18 - config.interestInflectionUtilX18).div(denom)
            );
    }
}
```

Alternatively, add an input validation in `addOrUpdateProduct` requiring `config.interestInflectionUtilX18 < ONE`.

---

### Proof of Concept

1. Owner calls `SpotEngine.addOrUpdateProduct` with `config.interestInflectionUtilX18 = 1e18` (`ONE`). No revert — no validation exists.
2. Users deposit and borrow the product until `totalBorrows == totalDeposits` (100% utilization).
3. Sequencer submits an `updateStates(dt)` transaction through the Endpoint.
4. Inside `_updateState`: `utilizationRatioX18 = totalBorrows.div(totalDeposits) = ONE`.
5. `ONE >= ONE` → the `else` branch is entered.
6. `ONE - config.interestInflectionUtilX18 = ONE - ONE = 0`.
7. `MathSD21x18.div(numerator, 0)` → `require(y != 0, ERR_DIV_BY_ZERO)` → **revert**.
8. The revert propagates through `updateStates`, aborting state updates for all products. Interest accrual is frozen protocol-wide. [6](#0-5)

### Citations

**File:** core/contracts/SpotEngineState.sol (L64-91)
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

**File:** core/contracts/libraries/MathSD21x18.sol (L62-65)
```text
    function div(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            require(y != 0, ERR_DIV_BY_ZERO);
            int256 result = (int256(x) * ONE_X18) / y;
```

**File:** core/contracts/SpotEngine.sol (L68-83)
```text
    function addOrUpdateProduct(
        uint32 productId,
        uint32 quoteId,
        int128 sizeIncrement,
        int128 minSize,
        Config calldata config,
        RiskHelper.RiskStore calldata riskStore
    ) public onlyOwner {
        bool isNewProduct = _addOrUpdateProduct(
            productId,
            quoteId,
            sizeIncrement,
            minSize,
            riskStore
        );
        configs[productId] = config;
```
