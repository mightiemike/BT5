### Title
`OracleValueStopLossExtension` Uses Arithmetic Mean Instead of Geometric Mean for Mid Price, Causing Stop-Loss Guard to Fail Open for `zeroForOne` Swaps — (`File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid price as the arithmetic mean `(bid + ask) / 2`, while every other component in the protocol uses the geometric mean `sqrt(bid * ask)`. By AM-GM inequality the arithmetic mean is always ≥ the geometric mean, so the mid price fed into the per-bin value metrics is systematically inflated. This deflates `metricT0` (the token0-denominated value per share) and inflates `metricT1`, causing the stop-loss watermark for `zeroForOne` swaps to be set lower than the true value, which allows LP funds to be drained beyond the configured drawdown before the guard triggers.

---

### Finding Description

In `_afterSwapOracleStopLoss`:

```solidity
// OracleValueStopLossExtension.sol line 218
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
``` [1](#0-0) 

The canonical mid price throughout the protocol is the geometric mean, computed by `SwapMath.midAndSpreadFeeX64FromBidAsk`:

```solidity
// SwapMath.sol line 70
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
``` [2](#0-1) 

`PriceVelocityGuardExtension.beforeSwap` correctly calls `SwapMath.midAndSpreadFeeX64FromBidAsk` to derive the geometric mid: [3](#0-2) 

`MetricOmmPoolDataProvider._marginalBestBidAsk` also uses `Math.sqrt(bid * ask)`: [4](#0-3) 

The per-bin value metrics are defined in the NatSpec as:

```
metricToken0 = t0*SCALE/shares + (t1 * 2^64 / mid) * SCALE / shares
metricToken1 = (t0 * mid / 2^64) * SCALE / shares + t1*SCALE/shares
``` [5](#0-4) 

Implemented in `_metrics`: [6](#0-5) 

Because `arith_mid ≥ geo_mid` (AM-GM), substituting the inflated arithmetic mean:

- **`metricT0`** is **understated**: the `t1 * Q64 / mid` term shrinks when `mid` is inflated.
- **`metricT1`** is **overstated**: the `t0 * mid / Q64` term grows when `mid` is inflated.

The watermark ratchet in `_checkAndUpdateWatermarks` sets `hwm0` to the live `metricT0` on first touch: [7](#0-6) 

Because `metricT0` is understated, `hwm0` is anchored below the true value. The drawdown floor `hwm0 * floorMultiplier / E6` is therefore lower than the admin intended. The stop-loss for `zeroForOne` swaps (which checks `breach0 && zeroForOne`) will not trigger until the metric falls below this deflated floor, allowing LP token1 to be drained further than the configured drawdown permits.

---

### Impact Explanation

The stop-loss is an after-swap hook: if it reverts, the entire swap transaction reverts and LP funds are protected. When the floor is deflated by the arithmetic-mean error, a swap that should have been blocked (metric below the correct floor but above the incorrect floor) executes successfully and LP token1 leaves the pool. The magnitude of the error is:

```
relative_error ≈ (spread/2)² / (2 * mid²)
```

For a 10% total oracle spread (bid = 0.95·mid, ask = 1.05·mid), the arithmetic mean exceeds the geometric mean by ≈ 0.12%. For a pool with $5M in liquidity and a 10% configured drawdown, this allows an additional ≈ $6,000 of LP principal to be drained per drawdown event beyond what the admin configured. For wider spreads or larger pools the loss scales accordingly.

---

### Likelihood Explanation

This affects every pool that deploys `OracleValueStopLossExtension` with a non-zero oracle spread. The error is present on every swap that touches the extension. Any public trader can time swaps to exploit the deflated floor without any privileged access. The trigger is the normal swap path through `MetricOmmPool.swap → _afterSwap → OracleValueStopLossExtension.afterSwap`. [8](#0-7) 

---

### Recommendation

Replace the arithmetic mean with the geometric mean, consistent with the rest of the protocol:

```solidity
// Before (incorrect):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(
    uint256(bidPriceX64), uint256(askPriceX64)
);
```

This aligns the stop-loss metric with the same mid price used by the swap engine and the velocity guard.

---

### Proof of Concept

Given `bid = 0.9 * Q64` and `ask = 1.1 * Q64` (10% spread each side):

```
Arithmetic mean mid = (0.9 + 1.1) / 2 * Q64 = 1.0 * Q64
Geometric mean mid  = sqrt(0.9 * 1.1) * Q64 = sqrt(0.99) * Q64 ≈ 0.99499 * Q64
```

With `t0 = 0`, `t1 = 1000`, `shares = 1000`:

```
metricT0 (arithmetic) = t1 * Q64 / (1.0 * Q64) / shares = 1000 / 1000 = 1.0
metricT0 (geometric)  = t1 * Q64 / (0.99499 * Q64) / shares ≈ 1.00504
```

The watermark is anchored at `1.0` instead of `1.00504`. With a 10% drawdown (`floorMultiplier = 0.9`):

```
Incorrect floor = 1.0 * 0.9 = 0.9
Correct floor   = 1.00504 * 0.9 ≈ 0.90454
```

A swap that drops `metricT0` to `0.902` would be blocked by the correct floor (`0.902 < 0.90454`) but passes the incorrect floor (`0.902 > 0.9`), allowing LP token1 to leave the pool beyond the configured drawdown.

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L17-28)
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
///
///      Watermarks decay linearly at decayPerSecondE8 (lazy, per bin). Guarantee: value per
///      share at oracle marks cannot fall faster than drawdown (one-time) + decay * t (ongoing).
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

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L283-283)
```text
      midPriceX64 = Math.sqrt(uint256(bidFromOracleX64) * uint256(askFromOracleX64));
```

**File:** metric-core/contracts/MetricOmmPool.sol (L280-295)
```text
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
