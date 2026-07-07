### Title
Stale Maker Price Used to Reconstruct `alreadyMatched` Quote Causes Taker Fee Miscalculation Across Multi-Fill Orders — (`core/contracts/OffchainExchange.sol`)

---

### Summary

In `OffchainExchange.matchOrders`, the `alreadyMatched` parameter passed to `applyFee` is reconstructed from the **current maker's price** multiplied by the stored **base amount already filled**. Because `filledAmounts` tracks only the base quantity (not the quote actually paid), and because a taker order can be filled by multiple makers at different prices, the reconstructed quote is stale whenever the current fill price differs from prior fill prices. This causes the minimum-fee threshold logic inside `applyFee` to compute an incorrect `feeApplied`, resulting in systematic fee overcharge or undercharge on every subsequent fill of a partially-filled taker order.

---

### Finding Description

`filledAmounts[digest]` accumulates the **base** amount matched for a taker order across all fills:

```solidity
// OffchainExchange.sol line 832-834
if (taker.order.sender != X_ACCOUNT) {
    filledAmounts[ordersInfo.taker.digest] += ordersInfo.taker.amountDelta;
}
```

When the same taker order is matched again, `alreadyMatched` is reconstructed as:

```solidity
// OffchainExchange.sol line 770-777
applyFee(
    callState.productId,
    ordersInfo.taker,
    market,
    -maker.order.priceX18.mul(filledAmounts[ordersInfo.taker.digest]),  // ← stale price
    taker.order.appendix,
    true
);
```

This uses the **current** maker's price `maker.order.priceX18` to convert the stored base amount into a quote amount. However, the actual quote already matched was computed at the **previous** maker's price(s). The two values diverge whenever different makers fill the same taker order at different prices — a routine occurrence in a CLOB.

Inside `applyFee`, the stale `alreadyMatched` feeds directly into the fee threshold calculation:

```solidity
// OffchainExchange.sol line 536-544
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

The intent is to apply the regular fee rate only on the portion of the order above `market.minSize` in quote terms. Because `alreadyMatched` is wrong, `feeApplied` is wrong, and the fee charged on the current fill is wrong.

**Structural parallel to the external report:** The external report's bug uses the *current* voting power to subtract an *old* contribution that was recorded at a *lower* voting power, causing underflow. Here, the *current* maker price is used to reconstruct an *old* quote contribution that was recorded at a *different* price, corrupting the fee threshold arithmetic. Both share the same root cause: a changing multiplier (voting power / price) is applied to a stored quantity (old vote weight / filled base) to undo a prior contribution, but the multiplier has changed since the contribution was recorded.

---

### Impact Explanation

**Fee overcharge (current price > previous fill prices):** `|alreadyMatched|` is overstated. `feeApplied` is larger than correct. The taker pays more fee than the protocol's fee schedule requires.

**Fee undercharge (current price < previous fill prices):** `|alreadyMatched|` is understated. `feeApplied` is smaller than correct, potentially reaching zero even when the taker's cumulative quote exceeds `minSize`. The taker pays less fee than required, directly reducing protocol revenue.

In the extreme undercharge case, if `|alreadyMatched + matchQuote| < market.minSize` due to the price drop, `feeApplied ≤ 0` and the entire fill is fee-free despite the order's cumulative quote value exceeding the minimum threshold.

---

### Likelihood Explanation

Any taker order that is filled by more than one maker at different prices triggers this bug. In a CLOB, large taker orders routinely consume multiple maker orders at different price levels (price-time priority). This is not an edge case — it is the normal execution path for any order that walks the book. No privileged access, collusion, or special conditions are required; the bug fires automatically whenever `filledAmounts[digest] != 0` and the current maker's price differs from the price(s) at which prior fills occurred.

---

### Recommendation

Store the **quote** amount already matched alongside the base amount, or maintain a separate `filledQuoteAmounts` mapping. Pass the stored quote value directly as `alreadyMatched` instead of reconstructing it from the current price:

```solidity
// Replace:
-maker.order.priceX18.mul(filledAmounts[ordersInfo.taker.digest])

// With:
-filledQuoteAmounts[ordersInfo.taker.digest]
```

And update `filledQuoteAmounts` after each fill:

```solidity
filledQuoteAmounts[ordersInfo.taker.digest] += ordersInfo.taker.amountDelta.mul(maker.order.priceX18).abs();
```

This ensures the fee threshold is computed against the actual quote already matched, regardless of subsequent price movements.

---

### Proof of Concept

1. Taker places a buy order for 200 units at price ≤ 1000. `market.minSize = 100e18`.
2. **Fill 1**: Maker A fills 100 units at price 1000. Quote paid = 100 × 1000 = 100,000. `filledAmounts[digest] = 100`. Fee applied correctly on `|0 + (-100,000)| - 100,000 = 0` → flat minimum fee only.
3. **Fill 2**: Maker B fills 100 units at price 500 (market moved down). `alreadyMatched = -(500 × 100) = -50,000`. Actual already-matched quote = -100,000. `matchQuote = -(500 × 100) = -50,000`. `feeApplied = |(-50,000) + (-50,000)| - 100,000 = 100,000 - 100,000 = 0`. **Zero fee charged on Fill 2**, even though the taker's cumulative quote (150,000) far exceeds `minSize`.
4. The protocol loses the fee revenue on Fill 2 entirely. The taker effectively pays fees only on Fill 1. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** core/contracts/OffchainExchange.sol (L770-777)
```text
        applyFee(
            callState.productId,
            ordersInfo.taker,
            market,
            -maker.order.priceX18.mul(filledAmounts[ordersInfo.taker.digest]),
            taker.order.appendix,
            true
        );
```

**File:** core/contracts/OffchainExchange.sol (L831-840)
```text
        if (taker.order.sender != X_ACCOUNT) {
            filledAmounts[ordersInfo.taker.digest] += ordersInfo
                .taker
                .amountDelta;
        }
        if (maker.order.sender != X_ACCOUNT) {
            filledAmounts[ordersInfo.maker.digest] += ordersInfo
                .maker
                .amountDelta;
        }
```
