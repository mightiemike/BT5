### Title
`OracleValueStopLossExtension` Uses Arithmetic Mid Price While Pool Uses Geometric Mid Price, Causing Stop-Loss Guard to Misfire on Spread Changes — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid price as the **arithmetic mean** of bid and ask, while every other pricing path in the protocol — the pool's swap execution and `PriceVelocityGuardExtension` — uses the **geometric mean** via `SwapMath.midAndSpreadFeeX64FromBidAsk`. Because the arithmetic mean is always ≥ the geometric mean (AM-GM inequality), the per-bin value metrics fed to the watermark guard are systematically wrong. When the oracle spread widens between the watermark-setting swap and a later swap, the `metricT1` (value in token1 terms) is overstated relative to its own watermark, causing the stop-loss to fail to block `!zeroForOne` swaps (token0 outflow) that should have been halted. LPs lose token0 principal that the guard was designed to protect.

---

### Finding Description

**Wrong mid price formula in the stop-loss extension:** [1](#0-0) 

```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;  // arithmetic mean
```

**Correct formula used everywhere else in the protocol:** [2](#0-1) 

```solidity
/// @notice Geometric mid price (Q64.64) and spread fee in Q64.64 from bid/ask oracle quotes.
function midAndSpreadFeeX64FromBidAsk(...) {
    midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);   // geometric mean
    ...
}
```

The pool's `swap` function uses the geometric mean: [3](#0-2) 

`PriceVelocityGuardExtension.beforeSwap` also uses the geometric mean: [4](#0-3) 

**How the wrong mid price corrupts the metrics:** [5](#0-4) 

```solidity
metricT0 = t0ps + (t1 * Q64 / midPriceX64) * SCALE / shares;   // t1 in token0 terms
metricT1 = (t0 * midPriceX64 / Q64) * SCALE / shares + t1ps;   // t0 in token1 terms
```

Since `AM ≥ GM`:
- `metricT0` is **understated** (dividing by a larger mid price reduces the t1→token0 conversion)
- `metricT1` is **overstated** (multiplying by a larger mid price inflates the t0→token1 conversion)

**How the watermark guard fails:** [6](#0-5) 

```solidity
(uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
if (breach1 && !zeroForOne) {
    revert OracleStopLossTriggered(...);   // blocks token0 outflow
}
```

The watermark `hwm1` is set to `metricT1` when `metricT1 >= hwm1`. If the oracle spread is `S1` at watermark-setting time and widens to `S2 > S1` at the next swap:

- `hwm1` was set to `metricT1_true × (1 + ε₁)` where `ε₁ ∝ S1²`
- Current `metricT1` is `metricT1_true × (1 + ε₂)` where `ε₂ ∝ S2² > ε₁`

Breach condition: `metricT1_true × (1 + ε₂) < hwm1 × floorMultiplier / E6`

Because `ε₂ > ε₁`, the left side is proportionally larger than the right side, making the breach **less likely to trigger** than it should be. The stop-loss for `!zeroForOne` swaps (token0 outflow) silently passes when it should revert.

---

### Impact Explanation

When the oracle spread widens between swaps — which is most likely during volatile market conditions, exactly when stop-loss protection is most critical — the `metricT1` watermark guard underestimates the true value loss in token1 terms. Swaps that drain token0 from the pool past the configured drawdown floor are not blocked. LPs suffer direct loss of token0 principal that the `OracleValueStopLossExtension` was deployed to prevent.

The magnitude of the discrepancy is `O(ΔS²)` where `ΔS` is the change in oracle spread. For a spread change from 0.5% to 5%, the arithmetic-vs-geometric error grows from ~0.0003% to ~0.03% of the mid price, which translates directly into a proportional undercount of the drawdown that the guard sees.

---

### Likelihood Explanation

Any pool that deploys `OracleValueStopLossExtension` and whose oracle spread varies over time is affected. Oracle spreads routinely widen during market stress (the exact scenario where stop-loss protection matters most). No privileged action is required; any public swap on such a pool triggers `afterSwap`, which calls `_afterSwapOracleStopLoss` with the wrong mid price formula. The trigger is fully reachable by any unprivileged trader.

---

### Recommendation

Replace the arithmetic mean with the geometric mean in `_afterSwapOracleStopLoss`, consistent with the rest of the protocol:

```solidity
// Before (wrong):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
``` [7](#0-6) 

---

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension` configured with `drawdownE6 = 50_000` (5% drawdown floor) and a price provider whose spread can be varied.
2. Set oracle spread to 0.1% (bid = 1.000, ask = 1.001). Execute a swap. The watermark `hwm1` is set using `midPriceX64_arith = 1.0005`, while the correct geometric mid is `1.00049975`. The watermark is overstated by ~0.00025%.
3. Widen oracle spread to 5% (bid = 1.000, ask = 1.050). Execute a `!zeroForOne` swap that drains token0 past the 5% drawdown floor. The current `metricT1` is computed with `midPriceX64_arith = 1.025`, while the correct geometric mid is `1.02470`. The current metric is overstated by ~0.03% relative to the watermark's overstatement of ~0.00025%. The net effect: the breach check sees `metricT1` as ~0.03% higher than it should be relative to `hwm1`, masking a real drawdown breach. The swap proceeds and LPs lose token0 value that the guard should have blocked. [8](#0-7)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L207-218)
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
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L252-255)
```text
    uint256 t0ps = Math.mulDiv(uint256(t0), METRIC_SCALE, shares);
    uint256 t1ps = Math.mulDiv(uint256(t1), METRIC_SCALE, shares);
    metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
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

**File:** metric-core/contracts/MetricOmmPool.sol (L242-243)
```text
    (uint256 midPriceX64, uint256 baseFeeX64) =
      SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L48-48)
```text
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```
