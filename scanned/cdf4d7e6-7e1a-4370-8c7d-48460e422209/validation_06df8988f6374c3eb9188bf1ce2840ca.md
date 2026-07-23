### Title
Stop-Loss Extension Uses Arithmetic Mean Instead of Geometric Mean for Mid Price, Causing Guard Underestimation - (File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol)

---

### Summary

`OracleValueStopLossExtension._checkStopLoss` computes `midPriceX64` as the arithmetic mean of bid and ask prices. The core pool (`SwapMath.midAndSpreadFeeX64FromBidAsk`) uses the geometric mean. By the AM-GM inequality, arithmetic mean ≥ geometric mean for all bid ≠ ask. The stop-loss therefore always evaluates LP value at a higher mid price than the pool uses for actual swaps. This systematically underestimates `metricT0` (LP value in token0 terms), sets the high watermark below the true LP value, and allows the zeroForOne stop-loss guard to be bypassed when the oracle spread is non-trivial.

---

### Finding Description

**Stop-loss mid price (arithmetic mean):** [1](#0-0) 

```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
```

**Pool mid price (geometric mean):** [2](#0-1) 

```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
baseFeeX64 = Math.mulDiv(askPriceX64, ONE_X64, midPriceX64, Math.Rounding.Ceil) - ONE_X64;
```

For any bid < ask, `(bid + ask)/2 > sqrt(bid * ask)`. The stop-loss always uses a strictly higher mid price than the pool.

The two per-share metrics are: [3](#0-2) 

```solidity
metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

A higher `midPriceX64` → lower `metricT0` (t1 converted to token0 terms is divided by a larger number) and higher `metricT1`. The watermark ratchets up on new highs: [4](#0-3) 

```solidity
function _applyWatermark(uint256 metric, uint256 hwm, uint256 floorMultiplier)
    private pure returns (uint256 newHwm, bool breached)
{
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
    return (hwm, breached);
}
```

Because `metricT0` is underestimated at watermark-setting time, the stored `hwmS.token0` is lower than the true LP value. The breach floor `(hwm0 * floorMultiplier) / E6` is therefore also lower. When the actual LP value later falls, it is compared against a depressed floor, so the guard fails to trigger even though the true value has crossed the configured drawdown threshold.

The breach check and state update: [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L218-218)
```text
    uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L252-255)
```text
    uint256 t0ps = Math.mulDiv(uint256(t0), METRIC_SCALE, shares);
    uint256 t1ps = Math.mulDiv(uint256(t1), METRIC_SCALE, shares);
    metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L268-284)
```text
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
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L328-335)
```text
  function _applyWatermark(uint256 metric, uint256 hwm, uint256 floorMultiplier)
    private
    pure
    returns (uint256 newHwm, bool breached)
  {
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
    return (hwm, breached);
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L70-71)
```text
    midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
    baseFeeX64 = Math.mulDiv(askPriceX64, ONE_X64, midPriceX64, Math.Rounding.Ceil) - ONE_X64;
```
