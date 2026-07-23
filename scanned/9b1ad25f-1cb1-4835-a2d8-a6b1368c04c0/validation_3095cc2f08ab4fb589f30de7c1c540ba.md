### Title
Stop-Loss Mid-Price Uses Arithmetic Mean While Pool Swap Math Uses Geometric Mean, Causing Systematic Guard Miscalibration - (File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid price as the **arithmetic mean** of bid and ask, while the pool's swap math (`SwapMath.midAndSpreadFeeX64FromBidAsk`) and the sibling `PriceVelocityGuardExtension` both use the **geometric mean**. By the AM-GM inequality the arithmetic mean is always ≥ the geometric mean, so the stop-loss consistently evaluates per-share metrics at a price that is higher than the price the pool actually used to settle the swap. This inflates `metricT1` (value in token1 terms), making the guard systematically less sensitive to token1-side value leakage and allowing harmful `zeroForOne == false` swaps to proceed past the configured drawdown floor.

---

### Finding Description

In `_afterSwapOracleStopLoss`:

```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;   // arithmetic mean
``` [1](#0-0) 

The pool's swap math computes the mid price as the geometric mean:

```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);   // geometric mean
``` [2](#0-1) 

`PriceVelocityGuardExtension.beforeSwap` correctly calls `SwapMath.midAndSpreadFeeX64FromBidAsk` (geometric mean):

```solidity
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
``` [3](#0-2) 

The stop-loss per-share metrics are:

```
metricT0 = t0*SCALE/shares + (t1 * Q64 / mid) * SCALE / shares
metricT1 = (t0 * mid / Q64) * SCALE / shares + t1*SCALE/shares
``` [4](#0-3) 

Because `mid_arithmetic ≥ mid_geometric` (AM-GM):

| Metric | Effect of arithmetic mean vs geometric mean |
|---|---|
| `metricT0` | **Lower** (t1 converted to token0 at a higher price → fewer token0 units) |
| `metricT1` | **Higher** (t0 converted to token1 at a higher price → more token1 units) |

The blocking logic is:

```solidity
if (breach0 && zeroForOne)  revert ...;   // blocks token1 outflow
if (breach1 && !zeroForOne) revert ...;   // blocks token0 outflow
``` [5](#0-4) 

An inflated `metricT1` means `breach1` is less likely to fire, so `zeroForOne == false` swaps (token0 in, token1 out) that should be blocked by the drawdown floor are allowed to proceed.

---

### Impact Explanation

The stop-loss invariant documented in the contract is:

> "value per share at oracle marks cannot fall faster than drawdown (one-time) + decay × t (ongoing)" [6](#0-5) 

Because `metricT1` is computed at a price higher than the pool's actual settlement price, the guard compares the watermark floor against an inflated metric. The actual per-share value in token1 terms (at the geometric mean the pool used) can fall below the configured floor without triggering the stop-loss. LPs continue to lose token1 value through swaps that the guard was configured to block. The magnitude of the discrepancy is `(AM − GM) / GM ≈ s²/8` where `s` is the fractional bid-ask spread; for a 10 % spread this is ≈ 0.125 %, for a 50 % spread ≈ 3.1 %. The bias is systematic and always in the direction that suppresses the token1-side guard.

---

### Likelihood Explanation

Every public swap on a pool that has `OracleValueStopLossExtension` configured in its `afterSwap` order triggers this path. No special permissions or setup are required beyond the pool existing with the extension active. The discrepancy grows with the oracle spread, which is largest precisely when the guard is most needed (volatile or manipulated markets).

---

### Recommendation

Replace the arithmetic mean with the geometric mean, consistent with the pool's swap math and `PriceVelocityGuardExtension`:

```solidity
// Before (arithmetic mean — wrong):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (geometric mean — consistent with SwapMath):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(
    uint256(bidPriceX64), uint256(askPriceX64)
);
``` [1](#0-0) [3](#0-2) 

---

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension` in the `afterSwap` slot, `drawdownE6 = 50_000` (5 % floor), and a 10 % oracle spread (e.g., `bid = 1.0e18`, `ask = 1.1e18` in Q64.64 units).
2. Seed the pool with token0 and token1 so that `metricT1` is just above the 5 % floor at the geometric mean price (`sqrt(1.0 × 1.1) ≈ 1.0488`).
3. Execute a `zeroForOne == false` swap. The extension computes `midPriceX64 = (1.0 + 1.1)/2 = 1.05`, which is ≈ 0.12 % higher than the geometric mean.
4. The inflated `midPriceX64` raises `metricT1` by ≈ 0.06 % (for a 50/50 bin), keeping it above the floor even though the actual value at the pool's settlement price is below the floor.
5. The stop-loss does not revert; the swap completes and LP token1 value leaks past the configured drawdown limit. [7](#0-6) [8](#0-7)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L27-28)
```text
///      Watermarks decay linearly at decayPerSecondE8 (lazy, per bin). Guarantee: value per
///      share at oracle marks cannot fall faster than drawdown (one-time) + decay * t (ongoing).
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L254-255)
```text
    metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L271-277)
```text
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L48-48)
```text
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```
