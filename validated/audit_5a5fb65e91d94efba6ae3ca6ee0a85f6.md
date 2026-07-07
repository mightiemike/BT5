### Title
`addOrUpdateProduct` Overwrites Interest Rate Config Without First Accruing Pending Interest — (`File: core/contracts/SpotEngine.sol`)

---

### Summary

`SpotEngine.addOrUpdateProduct` directly overwrites `configs[productId]` — which contains all interest rate parameters — without first triggering `updateStates` to settle the pending interest accrued under the old config. The next sequencer-driven `updateStates` call will then apply the new config parameters retroactively to the entire elapsed `dt`, corrupting the cumulative deposit and borrow multipliers for all users of the affected product.

---

### Finding Description

`SpotEngineState._updateState` reads `configs[productId]` at the moment it is called to compute the borrow rate and minimum deposit rate for the elapsed period `dt`:

```solidity
// SpotEngineState.sol lines 67–99
Config memory config = configs[productId];
int128 borrowerRateX18 = config.interestFloorX18;
// ... rate curve using config.interestSmallCapX18, interestLargeCapX18, interestInflectionUtilX18
minDepositRateX18 = config.minDepositRateX18;
``` [1](#0-0) 

`SpotEngine.addOrUpdateProduct` (callable by the owner at any time) unconditionally overwrites `configs[productId]` on line 83 without first settling pending interest:

```solidity
// SpotEngine.sol lines 68–97
function addOrUpdateProduct(...) public onlyOwner {
    bool isNewProduct = _addOrUpdateProduct(...);
    configs[productId] = config;   // ← overwrites config immediately
    ...
}
``` [2](#0-1) 

`updateStates` is `onlyEndpoint`, so the owner has no way to force accrual before changing the config:

```solidity
// SpotEngineState.sol line 265
function updateStates(uint128 dt) external onlyEndpoint {
``` [3](#0-2) 

When the sequencer next calls `updateStates(dt)`, `dt` covers the entire interval since the last accrual — including the portion that elapsed under the old config. The new config parameters are applied to the full `dt`, retroactively mispricing the interest for the pre-change window.

---

### Impact Explanation

The corrupted values are `state.cumulativeDepositsMultiplierX18` and `state.cumulativeBorrowsMultiplierX18`:

```solidity
// SpotEngineState.sol lines 129–137
state.cumulativeBorrowsMultiplierX18 = state.cumulativeBorrowsMultiplierX18.mul(borrowRateMultiplierX18);
state.cumulativeDepositsMultiplierX18 = state.cumulativeDepositsMultiplierX18.mul(depositRateMultiplierX18);
``` [4](#0-3) 

Every user balance for the product is denominated in normalized units and de-normalized through these multipliers:

```solidity
// SpotEngineState.sol lines 183–191
return Balance(balance.amountNormalized.mul(cumulativeMultiplierX18));
``` [5](#0-4) 

A concrete example: if `minDepositRateX18` is raised from 0 to a positive value, the next `updateStates` applies the new minimum deposit rate to the full `dt` (which may span hours or days), inflating `cumulativeDepositsMultiplierX18` beyond what depositors are entitled to for the pre-change period. The excess is drawn from the protocol's fee balance or from borrowers, constituting an accounting corruption. Conversely, lowering `interestFloorX18` or `interestSmallCapX18` before accrual silently under-pays depositors for the elapsed period.

---

### Likelihood Explanation

The owner legitimately calls `addOrUpdateProduct` to tune interest rate parameters as market conditions change — this is a routine governance action. The protocol provides no mechanism to enforce accrual before the config change (since `updateStates` is `onlyEndpoint`). Every config update to an existing product with non-zero deposits triggers this mispricing on the next sequencer tick. Likelihood is **medium**: it requires an owner action, but that action is expected and normal, and the impact is automatic.

---

### Recommendation

Before overwriting `configs[productId]` for an existing product, the protocol must settle pending interest. Since `updateStates` is `onlyEndpoint`, the recommended fix is to add an internal accrual path callable from `addOrUpdateProduct`, or to require the sequencer to submit an `updateStates` transaction atomically before any config change takes effect (e.g., via a two-step commit pattern or by exposing an internal `_updateState` call gated to the owner path for existing products only).

---

### Proof of Concept

1. Product `P` has `interestFloorX18 = 1e16` (1% floor). Last `updateStates` was at `T=0`.
2. At `T = 86400` (24 hours elapsed, `dt = 86400`), the owner calls `addOrUpdateProduct` for product `P` with `interestFloorX18 = 5e16` (5% floor). `configs[P]` is overwritten immediately.
3. At `T = 86401`, the sequencer calls `updateStates(86401)` (full elapsed `dt`).
4. `_updateState` reads `config.interestFloorX18 = 5e16` and computes the borrow rate for the entire 86401-second window at the new 5% floor — even though the rate was 1% for 86400 of those seconds.
5. `cumulativeBorrowsMultiplierX18` is inflated by ~4× the correct incremental amount for that day, overcharging all borrowers and over-crediting depositors for the pre-change period. [2](#0-1) [6](#0-5)

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

**File:** core/contracts/SpotEngineState.sol (L129-137)
```text
        state.cumulativeBorrowsMultiplierX18 = state
            .cumulativeBorrowsMultiplierX18
            .mul(borrowRateMultiplierX18);

        int128 depositRateMultiplierX18 = ONE + realizedDepositRateX18;

        state.cumulativeDepositsMultiplierX18 = state
            .cumulativeDepositsMultiplierX18
            .mul(depositRateMultiplierX18);
```

**File:** core/contracts/SpotEngineState.sol (L183-191)
```text
    ) internal pure returns (Balance memory) {
        int128 cumulativeMultiplierX18;
        if (balance.amountNormalized > 0) {
            cumulativeMultiplierX18 = state.cumulativeDepositsMultiplierX18;
        } else {
            cumulativeMultiplierX18 = state.cumulativeBorrowsMultiplierX18;
        }

        return Balance(balance.amountNormalized.mul(cumulativeMultiplierX18));
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

**File:** core/contracts/SpotEngine.sol (L68-97)
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

        if (isNewProduct) {
            require(productId != QUOTE_PRODUCT_ID);
            _setState(
                productId,
                State({
                    cumulativeDepositsMultiplierX18: ONE,
                    cumulativeBorrowsMultiplierX18: ONE,
                    totalDepositsNormalized: 0,
                    totalBorrowsNormalized: 0
                })
            );
        }
    }
```
