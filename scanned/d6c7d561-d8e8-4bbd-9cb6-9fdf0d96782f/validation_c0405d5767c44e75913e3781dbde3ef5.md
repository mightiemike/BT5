### Title
Stop-Loss Extension Uses Arithmetic Mid-Price While Pool Uses Geometric Mid-Price, Causing Guard Miscalibration — (File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid-price as the arithmetic mean of bid and ask, while every other pricing path in the protocol uses the geometric mean. By AM-GM inequality, arithmetic mean ≥ geometric mean for any non-zero spread. This causes the stop-loss to overestimate `metricToken1` and underestimate `metricToken0` relative to the pool's actual settled prices. When the oracle spread widens between watermark establishment and the next swap, the guard can fail to trigger even though the pool's actual per-share value has fallen below the configured drawdown floor, allowing LP principal to leak beyond the intended protection boundary.

---

### Finding Description

**Pool swap path — geometric mean:**

`MetricOmmPool.swap` calls `_getBidAndAskPriceX64()` then passes the result to `SwapMath.midAndSpreadFeeX64FromBidAsk`:

```solidity
// SwapMath.sol line 70
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
```

Every swap, every bin-price interpolation, and every fee calculation in the protocol is anchored to this geometric mid.

**Stop-loss afterSwap hook — arithmetic mean:**

`OracleValueStopLossExtension._afterSwapOracleStopLoss` receives the same `bidPriceX64` / `askPriceX64` forwarded by the pool but computes mid differently:

```solidity
// OracleValueStopLossExtension.sol line 218
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
```

This mid is then fed into `_metrics`:

```solidity
// OracleValueStopLossExtension.sol lines 254-255
metricToken0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
metricToken1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

For any spread > 0: `arith_mid > geo_mid`, so:
- `metricToken1` (t0 × mid / shares + t1/shares) is **inflated** relative to the pool's actual value
- `metricToken0` (t0/shares + t1/mid/shares) is **deflated** relative to the pool's actual value

**Watermark ratchet and breach check:**

```solidity
// OracleValueStopLossExtension.sol lines 270-278
(uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
if (breach0 && zeroForOne) { revert OracleStopLossTriggered(...); }

(uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
if (breach1 && !zeroForOne) { revert OracleStopLossTriggered(...); }
```

The watermark for token1 is ratcheted up to the inflated arithmetic metric. The floor is `hwm1 × floorMultiplier / E6`. When the spread subsequently widens (or was already wide at watermark time), the computed `metricToken1` remains inflated relative to the actual geometric-mid value, so the guard can fail to detect a real breach.

**Spread-change attack path:**

1. Watermark established during a period of small spread (arithmetic ≈ geometric). `hwm1` is set close to the true geometric value.
2. Oracle spread widens (volatile market, oracle latency, or oracle manipulation).
3. Attacker executes a `!zeroForOne` swap that causes value leakage (e.g., pool sells token0 at a stale low price).
4. Stop-loss computes `metricToken1` using arithmetic mid (now materially higher than geometric mid).
5. Computed `metricToken1` remains above `hwm1 × floorMultiplier / E6` even though the true geometric-mid value has fallen below the floor.
6. `OracleStopLossTriggered` is not emitted; the swap is not reverted; LP principal leaks beyond the configured drawdown.

---

### Impact Explanation

The stop-loss is the primary on-chain protection for LP capital against value leakage. When it fails to trigger, LPs lose principal beyond the drawdown they accepted. The magnitude of the error scales with the square of the spread: a 10 % spread produces ~0.25 % metric inflation; a 20 % spread produces ~1 % inflation. For pools configured with tight drawdowns (e.g., 1–5 %), a 10–20 % oracle spread can suppress the guard entirely at the threshold, constituting a direct loss of LP principal above the Sherlock medium threshold.

---

### Likelihood Explanation

Any pool that deploys `OracleValueStopLossExtension` is affected whenever the oracle spread is non-zero. Pools on volatile pairs or during periods of oracle stress (the exact conditions the stop-loss is meant to guard against) experience the largest spread and therefore the largest guard error. No privileged action is required; any public swap at the right moment triggers the path.

---

### Recommendation

Replace the arithmetic mean with the geometric mean, consistent with the rest of the protocol:

```solidity
// Before (line 218):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After:
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

This makes the stop-loss metrics consistent with the prices the pool actually used to settle the swap, eliminating the spread-dependent bias.

---

### Proof of Concept

```
Setup:
  bid  = 0.80 × 2^64,  ask = 1.20 × 2^64   (20 % spread)
  geometric mid  = sqrt(0.80 × 1.20) × 2^64 ≈ 0.9798 × 2^64
  arithmetic mid = (0.80 + 1.20) / 2 × 2^64 = 1.0000 × 2^64

  t0 = 1000, t1 = 1000, shares = 1000
  drawdown = 5 % → floorMultiplier = 0.95

Watermark established (small spread, arith ≈ geo):
  hwm1 ≈ 2.0  (t0×1.0/shares + t1/shares)

Spread widens to 20 %; bad !zeroForOne swap executes:
  t0 = 1000, t1 = 900  (100 units of token1 leaked)

Stop-loss check (arithmetic mid = 1.0):
  metricToken1 = 1000×1.0/1000 + 900/1000 = 1.90
  floor        = 2.0 × 0.95 = 1.90
  1.90 >= 1.90  →  NO BREACH  (guard silent, swap committed)

Correct check (geometric mid ≈ 0.9798):
  metricToken1 = 1000×0.9798/1000 + 900/1000 = 1.8798
  floor        = 2.0 × 0.95 = 1.90
  1.8798 < 1.90  →  BREACH  (guard should have reverted the swap)
```

The 20 % spread causes a ~1 % inflation in the arithmetic metric, which is enough to suppress the guard at a 5 % drawdown threshold, allowing the swap to commit and LP token1 value to leak beyond the configured protection boundary. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L326-336)
```text
  /// @dev Ratchet up on new highs; report breach below the drawdown floor. Direction-aware
  ///      blocking is decided by the caller.
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

**File:** metric-core/contracts/libraries/SwapMath.sol (L65-72)
```text
  function midAndSpreadFeeX64FromBidAsk(uint256 bidPriceX64, uint256 askPriceX64)
    internal
    pure
    returns (uint256 midPriceX64, uint256 baseFeeX64)
  {
    midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
    baseFeeX64 = Math.mulDiv(askPriceX64, ONE_X64, midPriceX64, Math.Rounding.Ceil) - ONE_X64;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
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
```
