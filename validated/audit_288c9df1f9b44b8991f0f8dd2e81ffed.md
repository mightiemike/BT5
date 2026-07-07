### Title
Global `MAX_DAILY_FUNDING_RATE` Constant Applied Uniformly Across All Perp Products Regardless of Volatility — (File: `core/contracts/PerpEngineState.sol`)

---

### Summary

`MAX_DAILY_FUNDING_RATE` is a hardcoded global constant (2% per day) applied identically to every perpetual product in `updateStates()`. The code itself acknowledges this is a known limitation. For volatile or low-liquidity perp markets, this uniform cap is insufficient to keep the mark price aligned with the index price, allowing traders to benefit from artificially suppressed funding payments at the direct financial expense of their counterparties.

---

### Finding Description

In `PerpEngineState.sol`, `MAX_DAILY_FUNDING_RATE` is declared as a file-level Solidity constant:

```solidity
// we will want to config this later, but for now this is global and a percentage
int128 constant MAX_DAILY_FUNDING_RATE = 20000000000000000; // 0.02
``` [1](#0-0) 

Inside `updateStates()`, this single constant is used to cap the funding rate for **every** perp product without distinction:

```solidity
int128 maxPriceDiff = MAX_DAILY_FUNDING_RATE.mul(indexPriceX18);

if (priceDiffX18.abs() > maxPriceDiff) {
    priceDiffX18 = (priceDiffX18 > 0) ? maxPriceDiff : -maxPriceDiff;
}
``` [2](#0-1) 

The resulting `paymentAmount` is then accumulated into `cumulativeFundingLongX18` and `cumulativeFundingShortX18` for that product:

```solidity
int128 paymentAmount = priceDiffX18.mul(dtX18).div(ONE_DAY_X18);
state.cumulativeFundingLongX18 += paymentAmount;
state.cumulativeFundingShortX18 += paymentAmount;
``` [3](#0-2) 

There is no per-product `maxFundingRate` field in the `State` struct or any other per-product configuration for this parameter. By contrast, the `SpotEngine` correctly uses a per-product `Config` struct for all interest rate parameters (`interestFloorX18`, `interestSmallCapX18`, `interestLargeCapX18`, etc.): [4](#0-3) 

The perp engine has no equivalent per-product configurability for its funding rate cap.

---

### Impact Explanation

When the actual mark-index price divergence for a volatile product exceeds 2% per day, the funding rate is silently capped at 2%. This has two concrete financial consequences:

1. **Corrupted `vQuoteBalance`:** The `_updateBalance` function computes each trader's funding settlement as `diffX18.mul(balance.amount)`, where `diffX18` is derived from the capped cumulative funding. If the cap suppresses the true funding rate, the `vQuoteBalance` of every position holder in that product is incorrect relative to what correct funding mechanics would produce. Traders on the "correct" side (e.g., shorts when mark > index) receive less funding than they are owed; traders on the "wrong" side (e.g., longs when mark > index) pay less than they should. [5](#0-4) 

2. **Persistent mark-index divergence:** Because the funding rate is insufficient to incentivize arbitrage, the mark price can remain persistently above or below the index price. This degrades the integrity of the perp market and exposes the protocol to cascading risk (e.g., incorrect health calculations, mispriced liquidations).

---

### Likelihood Explanation

**Medium.** The protocol lists multiple perp products with different volatility profiles. A 2% daily cap is reasonable for BTC or ETH perpetuals but is demonstrably insufficient for small-cap or newly listed altcoin perpetuals, which routinely experience intraday mark-index divergences exceeding 5–10%. The developer comment in the source code ("we will want to config this later") confirms this is a recognized gap, not a deliberate design choice.

---

### Recommendation

Move `MAX_DAILY_FUNDING_RATE` from a file-level constant into a per-product configurable field, analogous to how `SpotEngine` uses per-product `Config` structs. Add a per-product `maxDailyFundingRateX18` field to the perp product configuration and expose an admin-only setter (via the existing `onlyOwner` pattern used in `PerpEngine`) to update it per product. This mirrors the fix described in the referenced Morpho PR #557.

---

### Proof of Concept

1. Protocol lists a volatile perp product (e.g., a small-cap altcoin) where intraday mark-index divergence routinely reaches 5%.
2. The sequencer calls `updateStates()` with `avgPriceDiffs[i]` representing a 5% mark-index divergence.
3. `maxPriceDiff = MAX_DAILY_FUNDING_RATE.mul(indexPriceX18)` evaluates to 2% of the index price.
4. `priceDiffX18` is capped at 2% instead of the actual 5%.
5. `paymentAmount` is computed at 2% instead of 5% — longs pay 60% less funding than the market demands.
6. Shorts receive 60% less funding than they are owed; their `vQuoteBalance` is understated by `(5% - 2%) * indexPrice * dt / ONE_DAY * position_size`.
7. The mark-index gap persists because the funding signal is too weak to close it, compounding the loss over time.
8. A trader who is long in this product and aware of the cap can hold the position indefinitely, paying a fraction of the economically correct funding rate, at the direct expense of short counterparties.

### Citations

**File:** core/contracts/PerpEngineState.sol (L10-11)
```text
// we will want to config this later, but for now this is global and a percentage
int128 constant MAX_DAILY_FUNDING_RATE = 20000000000000000; // 0.02
```

**File:** core/contracts/PerpEngineState.sol (L34-36)
```text
        int128 diffX18 = cumulativeFundingAmountX18 -
            balance.lastCumulativeFundingX18;
        int128 deltaQuote = vQuoteDelta - diffX18.mul(balance.amount);
```

**File:** core/contracts/PerpEngineState.sol (L121-128)
```text
                int128 maxPriceDiff = MAX_DAILY_FUNDING_RATE.mul(indexPriceX18);

                if (priceDiffX18.abs() > maxPriceDiff) {
                    // Proper sign
                    priceDiffX18 = (priceDiffX18 > 0)
                        ? maxPriceDiff
                        : -maxPriceDiff;
                }
```

**File:** core/contracts/PerpEngineState.sol (L130-132)
```text
                int128 paymentAmount = priceDiffX18.mul(dtX18).div(ONE_DAY_X18);
                state.cumulativeFundingLongX18 += paymentAmount;
                state.cumulativeFundingShortX18 += paymentAmount;
```

**File:** core/contracts/SpotEngineState.sol (L66-99)
```text
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
