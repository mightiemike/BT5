### Title
`OracleValueStopLossExtension` Computes Mid-Price with Arithmetic Mean While Pool Uses Geometric Mean, Causing Stop-Loss Guard to Systematically Underprotect LPs - (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` derives `midPriceX64` as the arithmetic mean of bid and ask, while `MetricOmmPool.swap` derives the same quantity via `SwapMath.midAndSpreadFeeX64FromBidAsk` as the geometric mean. Because arithmetic mean ≥ geometric mean (AM-GM inequality), the per-share value metric for token1 (`metricT1`) is systematically overstated every time a swap touches the extension. This inflates the high-watermark stored for token1, causing the stop-loss to require a larger actual drawdown before it fires on `!zeroForOne` swaps (the direction that drains token1 from the pool).

---

### Finding Description

**Pool mid-price (geometric mean):**

`SwapMath.midAndSpreadFeeX64FromBidAsk`:
```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);   // geometric mean
``` [1](#0-0) 

**Stop-loss mid-price (arithmetic mean):**

`OracleValueStopLossExtension._afterSwapOracleStopLoss`:
```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;  // arithmetic mean
``` [2](#0-1) 

The extension then feeds this inflated mid into `_metrics`:

```solidity
metricT1 = _clampMetric(
    Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps
);
``` [3](#0-2) 

`metricT1` represents the bin's total value denominated in token1. The token0 component is `t0 * midPriceX64 / Q64`. Because `arithmeticMid > geometricMid`, this component is always larger than the value the pool itself would assign to the same token0 balance at the same oracle quote.

`_applyWatermark` ratchets the stored watermark up to the current metric whenever `metric >= hwm`:

```solidity
if (metric >= hwm) return (metric, false);
breached = metric < (hwm * floorMultiplier) / E6;
return (hwm, breached);
``` [4](#0-3) 

Every swap that sets a new high for `metricT1` stores an inflated watermark. The stop-loss for `!zeroForOne` swaps fires only when:

```solidity
if (breach1 && !zeroForOne) {
    revert OracleStopLossTriggered(...);
}
``` [5](#0-4) 

Because the stored watermark is inflated, the actual drawdown required to breach `(hwm * floorMultiplier) / E6` is larger than the admin-configured `drawdownE6`. The guard is systematically less protective than intended for the direction that drains token1.

The pool passes the same `bidPriceX64` / `askPriceX64` snapshot to both the swap math and the extension: [6](#0-5) 

So the discrepancy is not caused by a stale oracle read — it is a pure formula mismatch on the same live quote.

---

### Impact Explanation

**Quantitative bias (AM vs GM):**

| Oracle spread | Arithmetic mid | Geometric mid | `metricT1` overstatement |
|---|---|---|---|
| 1 % | mid | 0.99997 × mid | ~0.003 % |
| 5 % | mid | 0.99969 × mid | ~0.031 % |
| 10 % | mid | 0.99875 × mid | ~0.125 % |
| 20 % | mid | 0.99499 × mid | ~0.503 % |

For a pool configured with a 5 % drawdown floor and a 10 % oracle spread, the stop-loss for `!zeroForOne` swaps fires at ~5.125 % actual drawdown instead of 5 %. For a $1 M pool this is ~$1,250 of unprotected LP principal per trigger event. For a 20 % spread the unprotected gap is ~$5,030.

The bias is permanent and cumulative: every swap that sets a new watermark high embeds the inflated value, so the gap between the configured floor and the actual protection floor never closes without an admin-initiated watermark reset.

---

### Likelihood Explanation

Every pool that deploys `OracleValueStopLossExtension` with a non-zero `drawdownE6` and a non-zero oracle spread is affected. The extension is a reference implementation explicitly listed in the contest scope. Any public swap that moves the bin cursor and sets a new `metricT1` high triggers the watermark inflation. No special attacker capability is required — ordinary trading activity continuously inflates the watermarks.

---

### Recommendation

Replace the arithmetic mean with the geometric mean, matching the formula used by the pool's swap math:

```solidity
// Before (arithmetic mean — incorrect):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (geometric mean — matches SwapMath.midAndSpreadFeeX64FromBidAsk):
uint256 midPriceX64 = Math.sqrt(uint256(bidPriceX64) * uint256(askPriceX64));
```

This ensures the value metrics used by the stop-loss are computed at the same price the pool uses for swap settlement, eliminating the systematic watermark inflation.

---

### Proof of Concept

**Setup:** Pool with bid = 0.9 × mid, ask = 1.1 × mid (10 % spread). Admin sets `drawdownE6 = 50_000` (5 % floor). Bin holds `t0 = 1000`, `t1 = 0`, `shares = 1000`.

**Step 1 — First swap sets watermark:**

- Arithmetic mid = `(0.9 + 1.1) / 2 × mid = mid`
- Geometric mid = `sqrt(0.9 × 1.1) × mid ≈ 0.99875 × mid`
- `metricT1` (arithmetic) = `t0 × arithmeticMid / Q64 × SCALE / shares = 1 × mid × SCALE`
- `metricT1` (geometric) = `t0 × geometricMid / Q64 × SCALE / shares ≈ 0.99875 × mid × SCALE`
- Watermark stored: `hwm1 = 1 × mid × SCALE` (inflated by ~0.125 %)

**Step 2 — Attacker drains token1 via `!zeroForOne` swaps:**

- After drain, `metricT1` falls to `0.95 × mid × SCALE` (5 % actual drawdown)
- Floor = `hwm1 × floorMultiplier / E6 = 1 × mid × SCALE × 0.95 = 0.95 × mid × SCALE`
- Check: `metricT1 (0.95) < floor (0.95)` → **not breached** (equal, not less than)

With geometric mean the watermark would be `0.99875 × mid × SCALE`, floor = `0.94881 × mid × SCALE`, and `0.95 > 0.94881` — also not breached at exactly 5 %, but the effective protection threshold is shifted by the full 0.125 % gap, meaning the attacker can extract an additional ~0.125 % of pool value before the stop-loss fires.

The root cause — arithmetic mean in the extension vs geometric mean in the pool — is directly observable at: [2](#0-1) [1](#0-0)

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L70-71)
```text
    midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
    baseFeeX64 = Math.mulDiv(askPriceX64, ONE_X64, midPriceX64, Math.Rounding.Ceil) - ONE_X64;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L218-218)
```text
    uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L255-255)
```text
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L275-277)
```text
    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L333-335)
```text
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
    return (hwm, breached);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L228-295)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );

    (uint256 midPriceX64, uint256 baseFeeX64) =
      SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    SwapMath.InternalSwapParams memory params =
      SwapMath.InternalSwapParams({midPriceX64: midPriceX64, baseFeeX64: baseFeeX64, priceLimitX64: priceLimitX64});

    (int256 amount0Delta, int256 amount1Delta, uint256 protocolFeeAmount) =
      _executeSwap(zeroForOne, amountSpecified, params);

    if (zeroForOne) {
      if (amount1Delta < 0) {
        // casting to uint256 is safe because amount1Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken1(recipient, uint256(-amount1Delta));
      }

      uint256 balance0Before = balance0();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
    } else {
      if (amount0Delta < 0) {
        // casting to uint256 is safe because amount0Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken0(recipient, uint256(-amount0Delta));
      }

      uint256 balance1Before = balance1();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount1Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount1Delta > 0 && balance1Before + uint256(amount1Delta) > balance1()) {
        revert IncorrectDelta();
      }
    }

    uint256 packedSlot0Final = Slot0Library.loadPackedSlot0();
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );
```
