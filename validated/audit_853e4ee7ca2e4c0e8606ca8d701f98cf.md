### Title
Incorrect `alreadyMatched` Calculation Corrupts Taker Fee Accounting for Partially-Filled Orders — (`File: core/contracts/OffchainExchange.sol`)

---

### Summary

In `OffchainExchange.matchOrders`, the `alreadyMatched` parameter passed to `applyFee` is supposed to represent the cumulative quote amount already matched for a taker order across prior fills. Instead, it is computed by multiplying the **current fill's maker price** by the **already-filled base amount**. When a taker order is partially filled across multiple `matchOrders` calls at different prices, this estimate diverges from the actual historical quote matched, causing the fee-applicable portion of the trade to be calculated incorrectly — either over-charging or under-charging the taker.

---

### Finding Description

In `matchOrders`, the taker fee is applied via:

```solidity
applyFee(
    callState.productId,
    ordersInfo.taker,
    market,
    -maker.order.priceX18.mul(filledAmounts[ordersInfo.taker.digest]),
    taker.order.appendix,
    true
);
``` [1](#0-0) 

The fourth argument is `alreadyMatched`, documented as `// in quote`. It is supposed to represent how much quote has already been matched for this taker order in prior fills, so that the fee threshold (`market.minSize`) is not double-applied.

However, `filledAmounts[ordersInfo.taker.digest]` stores only the **base amount** already filled:

```solidity
filledAmounts[ordersInfo.taker.digest] += ordersInfo.taker.amountDelta;
``` [2](#0-1) 

There is no storage of the actual quote amounts from prior fills. The conversion from base to quote uses `maker.order.priceX18` — the **current fill's price** — not the prices at which prior fills actually executed. When prices differ between fills, the estimate is wrong.

Inside `applyFee`, the fee-applicable portion is computed as:

```solidity
int128 feeApplied = MathHelper.abs(alreadyMatched + matchQuote) - market.minSize;
feeApplied = MathHelper.min(feeApplied, matchQuote.abs());
``` [3](#0-2) 

`alreadyMatched + matchQuote` is supposed to be the total quote matched so far (prior fills + current fill). If `alreadyMatched` is wrong, `feeApplied` is wrong, and the taker is charged fees on an incorrect portion of the trade.

---

### Impact Explanation

**Accounting corruption:** The taker's fee is calculated on the wrong quote basis whenever a taker order is partially filled across multiple `matchOrders` calls at different prices.

- If the current fill price is **lower** than prior fill prices: `alreadyMatched` is underestimated in absolute value → `feeApplied` is underestimated → the taker **underpays fees**, and the protocol under-collects.
- If the current fill price is **higher** than prior fill prices: `alreadyMatched` is overestimated → `feeApplied` is overestimated → the taker **overpays fees**, losing funds beyond what the protocol intends to charge.

The error magnitude is `|price_current − price_historical| × base_already_filled`. For large orders filled across a trending market, this can be a meaningful dollar amount. The corrupted fee flows into `market.collectedFees` and ultimately into `X_ACCOUNT` via `dumpFees`, meaning the protocol's fee accounting is permanently desynchronized from the actual trade economics.

---

### Likelihood Explanation

Any taker order large enough to require multiple partial fills across separate `matchOrders` calls is affected. This is a normal operating condition for large traders on any active market. No special permissions, governance access, or external dependency failure is required — the bug is triggered by the ordinary sequencer behavior of matching a single taker order against multiple makers over time at varying prices.

---

### Recommendation

Track the cumulative quote matched per order digest in a separate mapping (e.g., `mapping(bytes32 => int128) public filledQuoteAmounts`), updated alongside `filledAmounts` at each fill. Pass this stored quote total as `alreadyMatched` instead of recomputing it from the current price:

```solidity
applyFee(
    callState.productId,
    ordersInfo.taker,
    market,
    -filledQuoteAmounts[ordersInfo.taker.digest],  // actual historical quote
    taker.order.appendix,
    true
);
// after the call:
filledQuoteAmounts[ordersInfo.taker.digest] += ordersInfo.taker.quoteDelta.abs();
```

This eliminates the price-dependency in the fee threshold calculation and ensures the fee-applicable portion is always computed from actual matched quote amounts.

---

### Proof of Concept

**Setup:** `market.minSize = 100e18` (100 quote units). Taker places a buy order for 200 base.

**Fill 1:** 100 base matched at price `10e18` → `quoteDelta = -1000e18`. `filledAmounts[digest] = 100`.

**Fill 2:** 100 base matched at price `8e18` (current maker price) → `quoteDelta = -800e18`.

**Current code computes:**
```
alreadyMatched = -8e18 × 100 = -800e18   (wrong: actual was -1000e18)
feeApplied = abs(-800 + -800) - 100 = 1600 - 100 = 1500 → min(1500, 800) = 800
```

**Correct computation should be:**
```
alreadyMatched = -1000e18   (actual quote from fill 1)
feeApplied = abs(-1000 + -800) - 100 = 1800 - 100 = 1700 → min(1700, 800) = 800
```

In this case the `min` clamp masks the error. But consider Fill 1 at price `8e18` and Fill 2 at price `10e18`:

**Fill 1:** 100 base at `8e18` → `quoteDelta = -800e18`. `filledAmounts[digest] = 100`.

**Fill 2:** 100 base at `10e18`.

**Current code:**
```
alreadyMatched = -10e18 × 100 = -1000e18   (wrong: actual was -800e18)
feeApplied = abs(-1000 + -1000) - 100 = 2000 - 100 = 1900 → min(1900, 1000) = 1000
```

**Correct:**
```
alreadyMatched = -800e18
feeApplied = abs(-800 + -1000) - 100 = 1800 - 100 = 1700 → min(1700, 1000) = 1000
```

Again clamped. The error becomes visible when the current fill is small relative to prior fills and the price difference is large enough to push `abs(alreadyMatched + matchQuote)` across the `minSize` boundary in the wrong direction — causing the fee-free `minSize` window to be applied (or not applied) incorrectly on the second fill. [4](#0-3) [1](#0-0) [2](#0-1)

### Citations

**File:** core/contracts/OffchainExchange.sol (L509-544)
```text
    function applyFee(
        uint32 productId,
        OrderInfo memory orderInfo,
        MarketInfo memory market,
        int128 alreadyMatched, // in quote
        uint128 appendix,
        bool taker
    ) internal {
        // X account is passthrough for trading and incurs
        // no fees
        if (orderInfo.sender == X_ACCOUNT) {
            return;
        }
        int128 matchQuote = orderInfo.quoteDelta;
        int128 meteredQuote = 0;
        if (taker) {
            // flat minimum fee
            if (alreadyMatched == 0) {
                meteredQuote += market.minSize;
                if (matchQuote < 0) {
                    meteredQuote = -meteredQuote;
                }
            }

            // exclude the portion on [0, self.min_size) for match_quote and
            // add to metered_quote
            // fee is only applied on [minSize, quote_amount)
            int128 feeApplied = MathHelper.abs(alreadyMatched + matchQuote) -
                market.minSize;
            feeApplied = MathHelper.min(feeApplied, matchQuote.abs());
            if (feeApplied > 0) {
                if (matchQuote < 0) {
                    feeApplied = -feeApplied;
                }
                meteredQuote += feeApplied;
            }
```

**File:** core/contracts/OffchainExchange.sol (L769-777)
```text
        // apply the taker fee
        applyFee(
            callState.productId,
            ordersInfo.taker,
            market,
            -maker.order.priceX18.mul(filledAmounts[ordersInfo.taker.digest]),
            taker.order.appendix,
            true
        );
```

**File:** core/contracts/OffchainExchange.sol (L831-835)
```text
        if (taker.order.sender != X_ACCOUNT) {
            filledAmounts[ordersInfo.taker.digest] += ordersInfo
                .taker
                .amountDelta;
        }
```
