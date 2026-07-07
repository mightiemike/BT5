### Title
Asymmetric `div`-based Fee Calculation Overcharges Taker Buyers — (`core/contracts/OffchainExchange.sol`)

---

### Summary

`applyFee()` in `OffchainExchange.sol` uses `meteredQuote.div(keepRateX18)` when `meteredQuote < 0` (taker buying, paying quote). This is the exact `revBp` equivalent: it treats the net trade amount as if it were the gross amount inclusive of fee, producing a fee of `|meteredQuote| * feeRate / (1 - feeRate)` instead of the correct `|meteredQuote| * feeRate`. The same function uses `meteredQuote.mul(keepRateX18)` for positive `meteredQuote` (taker selling, receiving quote), which correctly yields `fee = feeRate * amount`. The asymmetry means taker buyers are systematically overcharged relative to taker sellers on every matched order.

---

### Finding Description

In `applyFee()`:

```solidity
int128 keepRateX18 = ONE - feeInfo.feeRate;
int128 newMeteredQuote = (meteredQuote > 0)
    ? meteredQuote.mul(keepRateX18)
    : meteredQuote.div(keepRateX18);
orderInfo.fee = meteredQuote - newMeteredQuote;
```

`meteredQuote` is derived directly from `orderInfo.quoteDelta` — the raw, net trade amount before any fee is applied. It is **not** a gross amount inclusive of fee.

**Positive branch** (`meteredQuote > 0`, taker selling / receiving quote):
- `newMeteredQuote = meteredQuote × (1 − r)`
- `fee = meteredQuote × r` ← correct: fee is `feeRate` of the net amount

**Negative branch** (`meteredQuote < 0`, taker buying / paying quote):
- `newMeteredQuote = meteredQuote / (1 − r)` (more negative)
- `fee = meteredQuote − meteredQuote/(1−r) = |meteredQuote| × r/(1−r)` ← this is `revBp(|meteredQuote|, r)`, the reverse-basis-point formula

The `div` path is mathematically correct only when `meteredQuote` is already the gross amount (i.e., net + fee). Since `meteredQuote` here is the net trade amount, the `div` path inflates the fee by a factor of `1/(1−r)`. [1](#0-0) 

The `meteredQuote` for the taker is constructed from `matchQuote = orderInfo.quoteDelta`, which is set to `-ordersInfo.maker.quoteDelta` — the raw matched quote amount with no fee component: [2](#0-1) [3](#0-2) 

---

### Impact Explanation

**Impact: Low** — wrong fee accounting. Taker buyers pay `|meteredQuote| × r/(1−r)` in fees instead of `|meteredQuote| × r`. The excess fee collected per trade is `|meteredQuote| × r²/(1−r)`. At the default taker rate of 2 bps (`200_000_000_000_000` in X18), the overcharge is approximately `4×10⁻⁸` per unit of trade value — negligible per trade but systematic. At higher configured fee tiers the overcharge grows: at 100 bps it becomes ~1 bps of overcharge per trade. The excess accrues to the protocol's collected fees rather than being returned to the taker. [4](#0-3) 

---

### Likelihood Explanation

**Likelihood: High** — triggered on every matched order where the taker is buying (negative `quoteDelta`). This is one of the two most common order directions. Every call to `fillOrder` / `matchOrders` that results in a taker buy executes this path. [5](#0-4) 

---

### Recommendation

Replace the ternary with a uniform `mul` for both signs, since `meteredQuote` is always a net amount:

```solidity
// Before (asymmetric):
int128 newMeteredQuote = (meteredQuote > 0)
    ? meteredQuote.mul(keepRateX18)
    : meteredQuote.div(keepRateX18);

// After (symmetric, correct):
int128 newMeteredQuote = meteredQuote.mul(keepRateX18);
```

This yields `fee = meteredQuote × feeRate` for both signs, consistent with the positive-branch behavior and with the maker fee calculation.

---

### Proof of Concept

Let `feeRate = 200_000_000_000_000` (2 bps, `r = 0.0002`), `keepRate = ONE − r = 0.9998 × 10¹⁸`.

**Taker selling** (receives 1000 USDC, `meteredQuote = +1000e18`):
- `newMeteredQuote = 1000e18 × 0.9998 = 999.8e18`
- `fee = 0.2e18` (= 0.0002 × 1000 = correct 2 bps)

**Taker buying** (pays 1000 USDC, `meteredQuote = −1000e18`):
- `newMeteredQuote = −1000e18 / 0.9998 = −1000.2004...e18`
- `fee = −1000e18 − (−1000.2004e18) = +0.2004e18`
- Correct fee would be `0.2e18`; overcharge = `0.0004e18` ≈ 0.04 bps extra

The taker buyer pays `0.2004` in fees vs the taker seller's `0.2` — a systematic asymmetry on every buy-side taker fill. [6](#0-5)

### Citations

**File:** core/contracts/OffchainExchange.sol (L522-544)
```text
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

**File:** core/contracts/OffchainExchange.sol (L556-565)
```text
        int128 keepRateX18 = ONE - feeInfo.feeRate;
        int128 newMeteredQuote = (meteredQuote > 0)
            ? meteredQuote.mul(keepRateX18)
            : meteredQuote.div(keepRateX18);
        orderInfo.fee = meteredQuote - newMeteredQuote;
        orderInfo.builderFee = matchQuote.abs().mul(feeInfo.builderFeeRate);
        orderInfo.quoteDelta =
            orderInfo.quoteDelta -
            orderInfo.fee -
            orderInfo.builderFee;
```

**File:** core/contracts/OffchainExchange.sol (L760-763)
```text
        ordersInfo.maker.quoteDelta = ordersInfo.taker.amountDelta.mul(
            maker.order.priceX18
        );
        ordersInfo.taker.quoteDelta = -ordersInfo.maker.quoteDelta;
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

**File:** core/contracts/OffchainExchange.sol (L933-945)
```text
    function getTierFeeRateX18(uint32 tier, uint32 productId)
        public
        view
        returns (FeeRates memory)
    {
        if (nonDefaultFeeTierMask & (1 << tier) != 0) {
            return feeRates[tier][productId];
        }
        return
            FeeRates({
                makerRateX18: 0,
                takerRateX18: 200_000_000_000_000 // 2 bps
            });
```
