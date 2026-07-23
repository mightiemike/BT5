### Title
`OracleValueStopLossExtension` Uses Arithmetic Mean Instead of Geometric Mean for Mid-Price, Causing Stop-Loss Guard to Systematically Underprotect the `zeroForOne == false` Direction - (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid price as the **arithmetic mean** of bid and ask, while `MetricOmmPool.swap` and `SwapMath.midAndSpreadFeeX64FromBidAsk` use the **geometric mean**. Because AM ≥ GM always (AM-GM inequality), the stop-loss extension evaluates LP value at a systematically higher mid price than the pool used for actual swap settlement. This inflates `metricT1` (value in token1 terms), causing the stop-loss guard for the `zeroForOne == false` direction (token0 outflow) to fail to trigger when it should, allowing continued LP value drain beyond the configured drawdown floor.

### Finding Description

**Pool mid-price formula** (`SwapMath.midAndSpreadFeeX64FromBidAsk`, line 70):
```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);  // geometric mean
```

**Stop-loss extension mid-price formula** (`OracleValueStopLossExtension._afterSwapOracleStopLoss`, line 218):
```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;  // arithmetic mean
```

The pool passes the same `bidPriceX64` and `askPriceX64` to both `_beforeSwap` and `_afterSwap` hooks (captured once at the top of `swap`). The stop-loss extension receives the correct oracle quotes but derives a different mid price from them than the pool used for settlement.

The `_metrics` function then computes per-share value using this inflated mid:

```solidity
metricT1 = _clampMetric(
    Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps
);
```

Since `midPriceX64_AM > midPriceX64_GM`, the `t0 * midPriceX64 / Q64` term is inflated, making `metricT1` appear larger than it actually is at the pool's settlement price. The watermark ratchet then records this inflated value, and the breach check `metric < hwm * floorMultiplier / E6` is evaluated against an inflated baseline, masking genuine value loss.

**Direction-specific impact:**
- `metricT0` (divides by mid): AM > GM → `t1/AM < t1/GM` → `metricT0` is **underestimated** → stop-loss for `zeroForOne == true` triggers too aggressively (false positives, DoS-like but not fund-loss)
- `metricT1` (multiplies by mid): AM > GM → `t0*AM > t0*GM` → `metricT1` is **overestimated** → stop-loss for `zeroForOne == false` **fails to trigger** when it should (false negative, fund-loss path)

### Impact Explanation

The stop-loss guard for the `zeroForOne == false` direction (token0 outflow) is systematically weakened. An attacker can execute swaps that drain token0 from the pool during periods of elevated oracle spread, while the stop-loss guard is fooled by the inflated arithmetic mean into believing LP value is still above the drawdown floor.

The magnitude of the discrepancy is:

```
AM/GM - 1 ≈ spread² / 8
```

For a 10% oracle spread: ~0.125% extra loss permitted beyond the configured drawdown.  
For a 50% oracle spread: ~3.125% extra loss permitted beyond the configured drawdown.

For a pool with $1M in LP value and a 50% spread event (e.g., during oracle disruption or high-volatility period), the stop-loss fails to protect up to ~$31,250 beyond the configured floor. The attacker can time swaps to exploit exactly this window.

### Likelihood Explanation

Oracle spreads widen during high-volatility events, which are precisely the conditions when the stop-loss guard is most needed. The `PriceProvider` and `ProtectedPriceProvider` both allow `confidenceParam` to scale the spread, and the pool's `_getBidAndAskPriceX64` only validates `bid > 0` and `bid < ask` — it does not cap the spread width. Any valid swap that reaches the `afterSwap` hook can trigger this path. No privileged access is required; any public swapper can execute `zeroForOne == false` swaps during a wide-spread period.

### Recommendation

Replace the arithmetic mean in `_afterSwapOracleStopLoss` with the same geometric mean formula used by the pool:

```solidity
// Before (line 218):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After:
uint256 midPriceX64 = Math.sqrt(uint256(bidPriceX64) * uint256(askPriceX64));
```

This ensures the stop-loss evaluates LP value at exactly the same mid price the pool used for swap settlement, eliminating the systematic bias.

### Proof of Concept

1. Pool is configured with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5% drawdown floor).
2. Oracle reports `bid = 0.75 * mid_true`, `ask = 1.25 * mid_true` (50% spread, e.g., during high volatility).
   - AM = `mid_true` (exact)
   - GM = `sqrt(0.75 * 1.25) * mid_true = sqrt(0.9375) * mid_true ≈ 0.9683 * mid_true`
3. Attacker executes `zeroForOne == false` swaps, draining token0. After each swap, `_afterSwapOracleStopLoss` runs.
4. Actual LP value (at GM) has dropped 7.5% below watermark (exceeds 5% drawdown → should trigger stop-loss).
5. But `metricT1` is computed with AM, which is ~3.2% higher than GM. The extension sees `metricT1` at only ~4.3% below watermark → **no breach** → stop-loss does not trigger.
6. Attacker continues draining token0 for an additional ~3.2% of LP value beyond the configured floor before the stop-loss eventually triggers.

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L228-243)
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
```
