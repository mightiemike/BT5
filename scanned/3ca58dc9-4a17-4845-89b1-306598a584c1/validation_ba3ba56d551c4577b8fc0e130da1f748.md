### Title
Stop-Loss Guard Evaluates Per-Bin Metrics at Wrong Mid-Price Formula, Misaligning Protection Boundary with Actual Swap Settlement - (File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol)

### Summary
`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid-price as a plain arithmetic average `(bid + ask) / 2`, while the pool's swap settlement and the `PriceVelocityGuardExtension` both derive mid-price through `SwapMath.midAndSpreadFeeX64FromBidAsk(bid, ask)`. Because the two formulas produce different values whenever the bid/ask are not perfectly symmetric (which is the normal case after `marginStep` adjustments are applied), the stop-loss evaluates per-bin `metricToken0` and `metricToken1` at a price that does not match the price the swap actually settled at. This is the direct analog of M-12: a guard check uses the wrong (unadjusted) value instead of the correctly-derived one, causing the protection boundary to be misaligned with the real economic impact.

### Finding Description

In `OracleValueStopLossExtension._afterSwapOracleStopLoss`:

```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
``` [1](#0-0) 

This arithmetic mean is then fed into `_metrics` to compute per-bin value-per-share:

```solidity
metricT0 = t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares);
metricT1 = Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps;
``` [2](#0-1) 

In contrast, `PriceVelocityGuardExtension.beforeSwap` — and the pool's own swap settlement path — both use:

```solidity
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
``` [3](#0-2) 

And in `MetricOmmPool.swap`:

```solidity
(uint256 midPriceX64, uint256 baseFeeX64) =
    SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
``` [4](#0-3) 

The oracle price providers apply asymmetric `stepBidFactor`/`stepAskFactor` adjustments (e.g., `stepBidFactor = BPS_BASE_U - marginStep`, `stepAskFactor = BPS_BASE_U + marginStep`): [5](#0-4) 

After these adjustments, `bid_out` and `ask_out` are **not** symmetric around the oracle mid. Therefore `(bid_out + ask_out) / 2 ≠ SwapMath.midAndSpreadFeeX64FromBidAsk(bid_out, ask_out)`. The stop-loss guard evaluates bin metrics at the wrong mid-price.

### Impact Explanation

The per-bin metrics are:
- `metricToken0 ∝ t0 + t1 * Q64 / mid` — decreases as mid increases
- `metricToken1 ∝ t0 * mid / Q64 + t1` — increases as mid increases

When the arithmetic mean exceeds the SwapMath mid (which occurs when `marginStep > 0`), the stop-loss sees a **lower** `metricToken0` and a **higher** `metricToken1` than the actual settled values. Concretely:

- `breach0` (blocks `zeroForOne` swaps) triggers **more aggressively** than warranted → false positives, legitimate swaps reverted
- `breach1` (blocks `!zeroForOne` swaps) triggers **less aggressively** than warranted → **false negatives**, the stop-loss fails to block token0-in/token1-out swaps that have genuinely breached the drawdown floor

The false-negative case is the fund-impacting one: LP value is leaking in the `!zeroForOne` direction, the stop-loss should revert the swap, but because `metricToken1` is overstated at the wrong mid-price, the breach condition is not met and the swap proceeds. LPs suffer losses that the configured drawdown floor was designed to prevent. [6](#0-5) 

### Likelihood Explanation

- Any pool deploying `OracleValueStopLossExtension` with a non-zero `marginStep` price provider is affected.
- The trigger is a normal public swap in the `!zeroForOne` direction when the pool's bin metrics are near the drawdown floor.
- No privileged action is required; any unprivileged swapper can execute the swap that should have been blocked.
- The discrepancy grows with spread width and `|marginStep|`, so pools with wider configured spreads are more exposed.

### Recommendation

Replace the arithmetic average in `_afterSwapOracleStopLoss` with the same `SwapMath.midAndSpreadFeeX64FromBidAsk` call used by the pool and the velocity guard:

```solidity
// Before (wrong):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

This ensures the stop-loss evaluates per-bin metrics at exactly the same mid-price the swap settlement used, keeping the protection boundary aligned with the actual economic impact.

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension` and a price provider configured with `marginStep > 0` (e.g., `marginStep = 50e15`, i.e., 5% step).
2. Set `drawdownE6 = 50_000` (5% drawdown floor) and seed a bin with known `t0`, `t1`, `totalShares`.
3. Set the high watermark to the current `metricToken1` value computed at the **SwapMath mid** (the correct price).
4. Execute a `!zeroForOne` swap. The pool settles at `SwapMath.midAndSpreadFeeX64FromBidAsk(bid, ask)`.
5. Observe that the stop-loss computes `metricToken1` at `(bid + ask) / 2 > SwapMath mid`, producing an overstated metric that does not breach the floor, so the swap is not reverted.
6. Verify that if the same metric were computed at the SwapMath mid, it would fall below `hwm * (E6 - drawdownE6) / E6` and the swap would correctly revert with `OracleStopLossTriggered`. [7](#0-6)

### Citations

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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L48-48)
```text
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

**File:** metric-core/contracts/MetricOmmPool.sol (L242-243)
```text
    (uint256 midPriceX64, uint256 baseFeeX64) =
      SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L53-54)
```text
    uint256 internal immutable stepBidFactor; // BPS_BASE_U - marginStep
    uint256 internal immutable stepAskFactor; // BPS_BASE_U + marginStep
```
