### Title
`OracleValueStopLossExtension` Uses Arithmetic Mid-Price While Pool Uses Geometric Mid-Price, Miscalibrating the Stop-Loss Guard in Both Directions — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss()` computes the oracle mid-price as the **arithmetic mean** of bid and ask, while every other pricing path in the protocol — the pool's `swap()`, `simulateSwapAndRevert()`, `getSellAndBuyPrices()`, and the data-provider lens — computes it as the **geometric mean** via `SwapMath.midAndSpreadFeeX64FromBidAsk()`. Because AM ≥ GM (strictly when bid ≠ ask), the stop-loss guard evaluates per-share value metrics at a systematically different price than the pool uses for settlement, causing the guard to be too lenient in one swap direction (false negative — guard bypass) and too strict in the other (false positive — legitimate swap blocked).

---

### Finding Description

**Pool mid-price (geometric mean):**

`SwapMath.midAndSpreadFeeX64FromBidAsk` computes:
```
midPriceX64 = sqrt(bidPriceX64 * askPriceX64)   // geometric mean
``` [1](#0-0) 

This is used in `MetricOmmPool.swap()`: [2](#0-1) 

**Stop-loss mid-price (arithmetic mean):**

`OracleValueStopLossExtension._afterSwapOracleStopLoss()` computes:
```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
``` [3](#0-2) 

This `midPriceX64` is then fed into `_metrics()` to compute per-share value:
```solidity
metricT0 = t0ps + (t1 * Q64 / mid) * SCALE / shares   // mid in denominator
metricT1 = (t0 * mid / Q64) * SCALE / shares + t1ps   // mid in numerator
``` [4](#0-3) 

**The discrepancy:**

By the AM-GM inequality, `(bid + ask)/2 ≥ sqrt(bid * ask)`, with strict inequality whenever `bid < ask`. Let `mid_arith = (bid+ask)/2` and `mid_geo = sqrt(bid*ask)`.

- `metricToken0` has `mid` in the **denominator**: `mid_arith > mid_geo` → `metricToken0_arith < metricToken0_geo`. The guard sees a **lower** token0-denominated value than the pool's pricing implies → **false positives** blocking `zeroForOne` swaps.
- `metricToken1` has `mid` in the **numerator**: `mid_arith > mid_geo` → `metricToken1_arith > metricToken1_geo`. The guard sees a **higher** token1-denominated value than the pool's pricing implies → **false negatives** failing to block `!zeroForOne` swaps that drain LP value below the configured floor.

The `_checkAndUpdateWatermarks` function then compares these miscalibrated metrics against the drawdown floor: [5](#0-4) 

---

### Impact Explanation

**False negative (guard bypass — LP value drain):** When `!zeroForOne` swaps drain token0 from the pool, the stop-loss is supposed to block further swaps once `metricToken1` falls below `hwm1 * (1 - drawdownE6/1e6)`. Because the arithmetic mid inflates `metricToken1` relative to the pool's actual geometric mid, the guard computes a metric that is higher than the true value. The guard therefore fails to trigger at the correct threshold, allowing LP principal to drain below the configured drawdown floor. The magnitude of the error is proportional to `t0 * (mid_arith - mid_geo) / Q64 * METRIC_SCALE / shares`, which grows with the oracle spread.

**False positive (core swap DoS):** For `zeroForOne` swaps, the arithmetic mid deflates `metricToken0`, causing the guard to trigger prematurely and revert legitimate swaps that the pool's own pricing would have permitted.

Both impacts are fund-relevant: the false negative directly violates the LP protection invariant the extension is designed to enforce; the false positive breaks core swap functionality.

---

### Likelihood Explanation

- Triggered by any public `swap()` call on a pool that has `OracleValueStopLossExtension` configured with a non-zero `drawdownE6`.
- No special permissions required; any trader can execute a swap.
- The discrepancy is always present when `bid < ask` (i.e., whenever the oracle returns a non-zero spread), which is the normal operating condition.
- The error magnitude scales with the oracle spread: for a 1% half-spread the difference is ~0.0025%; for a 5% half-spread it is ~0.125%. Pools with wider spreads (volatile assets) are more severely affected.

---

### Recommendation

Replace the arithmetic mean in `_afterSwapOracleStopLoss` with the same geometric mean formula used by the pool:

```diff
--- a/metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol
+++ b/metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol
@@ -215,7 +215,8 @@ contract OracleValueStopLossExtension is BaseMetricExtension, IOracleValueStopLo
     PoolStopLossConfig memory cfg = oracleStopLossConfig[pool_];
     uint256 drawdown = cfg.drawdownE6;
     if (drawdown == 0) return;
-    uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
+    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

This ensures the stop-loss evaluates LP value at exactly the same mid-price the pool used for swap settlement, making the guard's drawdown floor consistent with the pool's own pricing invariant.

---

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5%), and an oracle with a 10% spread: `bid = 0.95 * P`, `ask = 1.05 * P`.
2. Geometric mid (pool): `mid_geo = sqrt(0.95P * 1.05P) = P * sqrt(0.9975) ≈ 0.99875P`
3. Arithmetic mid (stop-loss): `mid_arith = (0.95P + 1.05P)/2 = P`
4. With bin state `t0 = 1000, t1 = 1000, shares = 1000`:
   - `metricToken1_geo = (1000 * 0.99875P / Q64) * SCALE/1000 + 1000*SCALE/1000 ≈ 0.99875P_scaled + 1`
   - `metricToken1_arith = (1000 * P / Q64) * SCALE/1000 + 1000*SCALE/1000 = P_scaled + 1`
5. After a `!zeroForOne` swap drains token0 such that the true `metricToken1_geo` falls below `hwm1 * 0.95` (the 5% drawdown floor), the stop-loss should trigger. But `metricToken1_arith` remains above the floor because it is inflated by `~0.125%` relative to the geometric metric, so the guard does not revert.
6. LP value has drained below the configured protection floor without the guard activating. [6](#0-5) [1](#0-0) [2](#0-1)

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L64-72)
```text
  /// @notice Geometric mid price (Q64.64) and spread fee in Q64.64 from bid/ask oracle quotes.
  function midAndSpreadFeeX64FromBidAsk(uint256 bidPriceX64, uint256 askPriceX64)
    internal
    pure
    returns (uint256 midPriceX64, uint256 baseFeeX64)
  {
    midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
    baseFeeX64 = Math.mulDiv(askPriceX64, ONE_X64, midPriceX64, Math.Rounding.Ceil) - ONE_X64;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L242-245)
```text
    (uint256 midPriceX64, uint256 baseFeeX64) =
      SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    SwapMath.InternalSwapParams memory params =
      SwapMath.InternalSwapParams({midPriceX64: midPriceX64, baseFeeX64: baseFeeX64, priceLimitX64: priceLimitX64});
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L207-243)
```text
  function _afterSwapOracleStopLoss(
    address pool_,
    uint256 packedSlot0Initial,
    uint256 packedSlot0Final,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bool zeroForOne
  ) internal {
    PoolStopLossConfig memory cfg = oracleStopLossConfig[pool_];
    uint256 drawdown = cfg.drawdownE6;
    if (drawdown == 0) return;
    uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
    uint256 minShares = IMetricOmmPool(pool_).getImmutables().minimalMintableLiquidity;
    if (minShares == 0) minShares = 1;
    PoolSlot0 memory s0 = Slot0Library.unpack(packedSlot0Initial);
    PoolSlot0 memory s1 = Slot0Library.unpack(packedSlot0Final);
    int8 lo = s0.curBinIdx < s1.curBinIdx ? s0.curBinIdx : s1.curBinIdx;
    int8 hi = s0.curBinIdx > s1.curBinIdx ? s0.curBinIdx : s1.curBinIdx;
    // forge-lint: disable-next-line(unsafe-typecast)
    uint256 count = uint256(int256(hi) - int256(lo) + 1);
    int8[] memory binIdxs = new int8[](count);
    for (uint256 i = 0; i < count; i++) {
      // forge-lint: disable-next-line(unsafe-typecast)
      binIdxs[i] = int8(int256(lo) + int256(i));
    }
    bytes32[] memory states = PoolStateLibrary._multipleBinStates(pool_, binIdxs);
    bytes32[] memory shares = PoolStateLibrary._multipleBinTotalShares(pool_, binIdxs);
    uint256 floorMultiplier = E6 - drawdown;
    uint256 decayRate = cfg.decayPerSecondE8;
    for (uint256 i = 0; i < count; i++) {
      uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
      if (totalShares == 0) continue;
      (uint104 t0, uint104 t1,,,) = PoolStateLibrary._decodeBinState(states[i]);
      (uint256 metricT0, uint256 metricT1) = _metrics(t0, t1, totalShares, minShares, midPriceX64);
      _checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1, floorMultiplier, decayRate, zeroForOne);
    }
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L246-256)
```text
  function _metrics(uint104 t0, uint104 t1, uint256 totalShares, uint256 minShares, uint256 midPriceX64)
    private
    pure
    returns (uint256 metricT0, uint256 metricT1)
  {
    uint256 shares = totalShares < minShares ? minShares : totalShares;
    uint256 t0ps = Math.mulDiv(uint256(t0), METRIC_SCALE, shares);
    uint256 t1ps = Math.mulDiv(uint256(t1), METRIC_SCALE, shares);
    metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L270-278)
```text
    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
    }
```
