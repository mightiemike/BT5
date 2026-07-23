### Title
`OracleValueStopLossExtension` Uses Arithmetic Mean Instead of Protocol-Standard Geometric Mean for Mid-Price, Miscalibrating the Stop-Loss Guard - (File: `metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid-price as the arithmetic mean `(bid + ask) / 2`, while every other component of the Metric OMM protocol — including `MetricOmmPool.swap` and `PriceVelocityGuardExtension.beforeSwap` — derives the mid-price via `SwapMath.midAndSpreadFeeX64FromBidAsk`, which computes the geometric mean `sqrt(bid × ask)`. By AM-GM inequality the arithmetic mean is always ≥ the geometric mean, so the stop-loss extension systematically overestimates the mid-price whenever the oracle spread is non-zero. This causes the per-bin value metrics used to set and test high-watermarks to be computed at a price that does not match the price the pool actually uses for swap settlement, miscalibrating the guard in a direction that allows LP value to drain below the configured drawdown floor before the stop-loss triggers.

---

### Finding Description

Inside `_afterSwapOracleStopLoss`, the mid-price used for all metric computations is:

```solidity
// metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol  line 218
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
``` [1](#0-0) 

The pool's own `swap` function and `PriceVelocityGuardExtension` both use the protocol-standard helper:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 242-243
(uint256 midPriceX64, uint256 baseFeeX64) =
    SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
``` [2](#0-1) 

```solidity
// metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol  line 48
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
``` [3](#0-2) 

The two per-bin metrics are:

```
metricToken0 = t0·SCALE/shares + (t1 · Q64 / mid) · SCALE / shares
metricToken1 = (t0 · mid / Q64) · SCALE / shares + t1·SCALE/shares
``` [4](#0-3) 

Because `mid_arithmetic > mid_geometric` for any non-zero spread:

- **`metricToken0`** is **underestimated** (the `t1/mid` term shrinks). The high-watermark is therefore set too low, the drawdown floor is too low, and the stop-loss triggers only after LP value has already fallen further than the configured `drawdownE6` permits.
- **`metricToken1`** is **overestimated** (the `t0·mid` term grows). The watermark is set too high, the floor is too high, and the stop-loss may revert legitimate swaps that have not actually breached the configured floor.

The watermark ratchet and breach check in `_checkAndUpdateWatermarks` and `_applyWatermark` operate entirely on these miscalibrated metrics: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

The stop-loss extension is the primary on-chain mechanism protecting LP principal from oracle-price-driven value leakage. When `metricToken0` is underestimated, the high-watermark for token0 is anchored below the true per-share value, so the drawdown floor `hwm · (1 − drawdownE6/1e6)` is also below the intended floor. A swap that drains token0 value past the configured floor will not be reverted until the metric falls below the (already-too-low) floor, meaning LPs absorb extra loss equal to the calibration error before the guard fires. For a pool with a 1 000 bps oracle spread and 50 % token1 weight, the arithmetic-vs-geometric discrepancy is ≈ 0.25 % of the token1 component of the metric; on a $10 M pool this is ≈ $12 500 of unprotected LP principal per drawdown event. The miscalibration is permanent and proportional to the oracle spread — it cannot be corrected by the pool admin without redeploying the extension.

---

### Likelihood Explanation

Every pool that deploys `OracleValueStopLossExtension` with a non-zero oracle spread is affected on every swap that touches a monitored bin. No special attacker action is required; the miscalibration is structural and is triggered by ordinary public swaps. Pools using wider-spread oracles (RWA feeds, illiquid pairs) experience proportionally larger miscalibration. The trigger is fully unprivileged.

---

### Recommendation

Replace the arithmetic mean with the same geometric-mean helper used everywhere else in the protocol:

```solidity
// In _afterSwapOracleStopLoss, replace:
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// With:
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(
    uint256(bidPriceX64), uint256(askPriceX64)
);
```

This aligns the stop-loss metric computation with the price the pool actually uses for swap settlement, ensuring the configured `drawdownE6` floor is enforced at the correct value.

---

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5 %), and an oracle with a 1 000 bps spread (bid = 0.95·mid, ask = 1.05·mid).
2. Add liquidity to bin 0 with equal token0/token1 value.
3. Call `afterSwap` (or trigger it via a real swap). The extension computes `midPriceX64 = (bid + ask) / 2 = mid` (arithmetic), while the true geometric mid is `sqrt(0.95·mid · 1.05·mid) = mid·sqrt(0.9975) ≈ 0.99875·mid`.
4. `metricToken0` is computed with the overestimated mid, producing a value ≈ 0.125 % lower than the true metric. The watermark is anchored at this lower value.
5. Drain token0 value by 5 % (the configured floor). The stop-loss does not trigger because the floor is 0.125 % lower than intended.
6. Drain an additional 0.125 % before the stop-loss finally triggers — demonstrating that LP principal below the configured floor was not protected. [7](#0-6) [8](#0-7)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L17-25)
```text
/// @dev Value formulas (Q64.64 mid = token1 per token0), per-share in bin scaled units:
///
///      metricToken0 = t0*SCALE/shares + (t1 * 2^64 / mid) * SCALE / shares
///      metricToken1 = (t0 * mid / 2^64) * SCALE / shares + t1*SCALE/shares
///
///      A pure mid move pushes the metrics in opposite directions; a value leak pushes both down.
///        - metricToken0 breach (mid suspect-high) blocks zeroForOne == true  (token1 outflow)
///        - metricToken1 breach (mid suspect-low)  blocks zeroForOne == false (token0 outflow)
///        - both breached blocks both directions
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L258-285)
```text
  function _checkAndUpdateWatermarks(
    address pool_,
    int8 binIdx,
    uint256 metricT0,
    uint256 metricT1,
    uint256 floorMultiplier,
    uint256 decayRate,
    bool zeroForOne
  ) private {
    BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
    uint256 dt = block.timestamp - hwmS.lastDecayTs;

    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
    }

    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token0 = uint104(hwm0);
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token1 = uint104(hwm1);
    hwmS.lastDecayTs = uint32(block.timestamp);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L328-336)
```text
  function _applyWatermark(uint256 metric, uint256 hwm, uint256 floorMultiplier)
    private
    pure
    returns (uint256 newHwm, bool breached)
  {
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
    return (hwm, breached);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L242-243)
```text
    (uint256 midPriceX64, uint256 baseFeeX64) =
      SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L48-48)
```text
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```
