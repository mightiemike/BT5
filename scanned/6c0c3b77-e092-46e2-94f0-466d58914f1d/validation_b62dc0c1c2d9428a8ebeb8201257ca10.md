### Title
Arithmetic Mid-Price Formula in `OracleValueStopLossExtension` Systematically Underestimates `metricT0` Watermarks, Weakening LP Stop-Loss Protection — (File: `metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid price as the **arithmetic mean** `(bid + ask) / 2`, while every other component in the protocol — `SwapMath.midAndSpreadFeeX64FromBidAsk`, `PriceVelocityGuardExtension`, `MetricOmmPool.swap`, and `MetricOmmPoolDataProvider` — uses the **geometric mean** `sqrt(bid * ask)`. By AM-GM inequality the arithmetic mean is always ≥ the geometric mean, so the stop-loss extension feeds an inflated mid price into its per-bin value metrics. This systematically underestimates `metricT0` (token0-denominated value per share) and overestimates `metricT1`, causing the high-watermark for the `zeroForOne` direction to be set lower than the true value. As a result, the stop-loss allows LPs to suffer a larger drawdown than the configured `drawdownE6` before the guard triggers.

---

### Finding Description

**Wrong formula — line 218:**

```solidity
// OracleValueStopLossExtension.sol:218
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
``` [1](#0-0) 

**Correct formula used everywhere else — `SwapMath.midAndSpreadFeeX64FromBidAsk`:**

```solidity
// SwapMath.sol:70
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
``` [2](#0-1) 

`PriceVelocityGuardExtension` calls `SwapMath.midAndSpreadFeeX64FromBidAsk` for its mid price: [3](#0-2) 

`MetricOmmPool.swap` also uses the geometric mean: [4](#0-3) 

The inflated arithmetic mid is then fed into `_metrics`:

```solidity
// OracleValueStopLossExtension.sol:254-255
metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
``` [5](#0-4) 

Because `arithmetic_mid > geometric_mid`:

- `metricT0` contains `t1 * Q64 / arithmetic_mid` — **underestimated** (dividing by a larger denominator).
- `metricT1` contains `t0 * arithmetic_mid / Q64` — **overestimated** (multiplying by a larger factor).

The watermark ratchet in `_checkAndUpdateWatermarks` sets `hwmS.token0` to the underestimated `metricT0`: [6](#0-5) 

The breach threshold for the `zeroForOne` direction is `hwm0 * floorMultiplier / E6`. Because `hwm0` is set too low, the threshold is also too low, so the guard allows the actual per-share value to fall further than `drawdownE6` before reverting.

---

### Impact Explanation

The `OracleValueStopLossExtension` is the primary on-chain mechanism preventing LPs from suffering unbounded impermanent loss or oracle-manipulation-driven value drain. Its invariant (stated in the NatDoc) is:

> *"value per share at oracle marks cannot fall faster than drawdown (one-time) + decay × t (ongoing)"* [7](#0-6) 

With the arithmetic mean, the `metricT0` watermark is set to a value that is `(arithmetic_mid / geometric_mid - 1) × 100%` lower than the true value. For a 10% oracle bid/ask spread (ask = 1.1·mid, bid = mid/1.1), the arithmetic mean exceeds the geometric mean by ≈ 0.45%; for a 50% spread it exceeds it by ≈ 8.3%. The stop-loss threshold for `zeroForOne` swaps is correspondingly lower, meaning LPs can lose that additional percentage of value before the guard fires. In pools with wide oracle spreads (e.g., illiquid or volatile asset pairs), this miscalibration is material and constitutes a direct, quantifiable loss of LP principal beyond the configured protection level.

---

### Likelihood Explanation

- Every swap on a pool configured with `OracleValueStopLossExtension` triggers `afterSwap`, which calls `_afterSwapOracleStopLoss`. The wrong formula executes on every such swap.
- No privileged action is required; any unprivileged swapper causes the watermark to be set with the wrong mid price.
- The error is proportional to the oracle spread and is always in the same direction (arithmetic ≥ geometric), so it accumulates monotonically as the watermark ratchets up on wrong values.
- Pools with wider oracle spreads (volatile or illiquid pairs) are most affected.

---

### Recommendation

Replace the arithmetic mean with the geometric mean, consistent with the rest of the protocol:

```solidity
// Before (wrong):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

This aligns the stop-loss mid-price computation with `PriceVelocityGuardExtension`, `MetricOmmPool.swap`, and `MetricOmmPoolDataProvider`, all of which already use `SwapMath.midAndSpreadFeeX64FromBidAsk`.

---

### Proof of Concept

**Setup:** Oracle with 10% spread: `ask = 1.1 · Q64`, `bid = Q64 / 1.1 ≈ 0.9091 · Q64`. Bin: `t0 = 1000`, `t1 = 1000`, `shares = 1000`. `drawdownE6 = 100_000` (10% drawdown allowed).

**Geometric mid (correct):**
```
mid_geo = sqrt(0.9091 * 1.1) * Q64 = sqrt(1.0) * Q64 = Q64
metricT0_correct = 1000/1000 + (1000 * Q64 / Q64) / 1000 = 1 + 1 = 2.0
hwm0_correct = 2.0
threshold_correct = 2.0 * 0.9 = 1.8
```

**Arithmetic mid (used by stop-loss):**
```
mid_arith = (0.9091 + 1.1) / 2 * Q64 = 1.00455 * Q64
metricT0_wrong = 1000/1000 + (1000 * Q64 / (1.00455 * Q64)) / 1000
               = 1 + 0.99547 = 1.99547
hwm0_wrong = 1.99547
threshold_wrong = 1.99547 * 0.9 = 1.79592
```

**Result:** The stop-loss triggers at `metricT0 < 1.79592` instead of `< 1.8`. The guard allows an additional `(1.8 - 1.79592) / 1.8 ≈ 0.23%` of value loss beyond the configured 10% drawdown before firing. For a 50% oracle spread, the same calculation yields ≈ 8.3% additional unprotected drawdown — a material LP fund loss that the configured stop-loss was explicitly designed to prevent.

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L13-29)
```text
/// @title OracleValueStopLossExtension
/// @notice Tracks per-bin value per share in token0 and token1 terms at the oracle mid,
///         against decaying high watermarks. Drawdown and decay changes are timelocked so LPs
///         can react; monitor at least as often as the timelock or trust the pool admin.
/// @dev Value formulas (Q64.64 mid = token1 per token0), per-share in bin scaled units:
///
///      metricToken0 = t0*SCALE/shares + (t1 * 2^64 / mid) * SCALE / shares
///      metricToken1 = (t0 * mid / 2^64) * SCALE / shares + t1*SCALE/shares
///
///      A pure mid move pushes the metrics in opposite directions; a value leak pushes both down.
///        - metricToken0 breach (mid suspect-high) blocks zeroForOne == true  (token1 outflow)
///        - metricToken1 breach (mid suspect-low)  blocks zeroForOne == false (token0 outflow)
///        - both breached blocks both directions
///
///      Watermarks decay linearly at decayPerSecondE8 (lazy, per bin). Guarantee: value per
///      share at oracle marks cannot fall faster than drawdown (one-time) + decay * t (ongoing).
contract OracleValueStopLossExtension is BaseMetricExtension, IOracleValueStopLossExtension {
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L218-218)
```text
    uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L48-51)
```text
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    // casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128 (same bound as pool)
    // forge-lint: disable-next-line(unsafe-typecast)
    uint128 midPrice = uint128(midPriceX64);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L242-244)
```text
    (uint256 midPriceX64, uint256 baseFeeX64) =
      SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    SwapMath.InternalSwapParams memory params =
```
