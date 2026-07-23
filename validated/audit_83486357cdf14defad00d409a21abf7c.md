Audit Report

## Title
`OracleValueStopLossExtension` Uses Arithmetic Mid-Price Instead of Geometric Mid-Price, Causing `metricT0` Underestimation and Stop-Loss Bypass for `zeroForOne` Swaps — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid-price as `(bidPriceX64 + askPriceX64) / 2` (arithmetic mean), while the pool and `PriceVelocityGuardExtension` both derive it as `sqrt(bid * ask)` (geometric mean) via `SwapMath.midAndSpreadFeeX64FromBidAsk`. By AM-GM, the arithmetic mean is always ≥ the geometric mean, so dividing by the larger arithmetic mid systematically underestimates `metricT0`. This sets the high-watermark and its drawdown floor too low, allowing value-draining `zeroForOne` swaps to proceed past the configured loss threshold without triggering the stop-loss.

## Finding Description

**Root cause — two different mid-price formulas for the same bid/ask pair:**

`OracleValueStopLossExtension._afterSwapOracleStopLoss` at line 218 computes:
```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;  // arithmetic mean
``` [1](#0-0) 

`SwapMath.midAndSpreadFeeX64FromBidAsk`, used by the pool for all oracle mid-price derivation, computes:
```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);  // geometric mean
``` [2](#0-1) 

`PriceVelocityGuardExtension` correctly uses the geometric mean via the same library function:
```solidity
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
``` [3](#0-2) 

**How the wrong mid-price corrupts `metricT0`:**

The `_metrics` function computes the token1-in-token0 contribution as `t1 * Q64 / midPriceX64`: [4](#0-3) 

Because `arithmeticMid ≥ geometricMid`, dividing by the larger arithmetic mid yields a smaller `metricT0` than the pool's own valuation.

**How the underestimated watermark allows stop-loss bypass:**

The watermark and floor are both derived from the underestimated `metricT0`: [5](#0-4) 

The `_applyWatermark` function sets `hwm0 = metricT0` (underestimated) and checks `metric < (hwm * floorMultiplier) / E6`: [6](#0-5) 

A value-draining `zeroForOne` swap that reduces the true (geometric-mid-based) value below the correct floor may still produce an arithmetic-mid-based metric above the underestimated floor, so `breach0` remains `false` and the stop-loss does not revert.

**Existing guards are insufficient:** The `drawdown == 0` early-exit guard only skips the check entirely; it does not correct the mid-price formula. No other guard compensates for the arithmetic/geometric discrepancy.

## Impact Explanation

The `OracleValueStopLossExtension` is the primary on-chain mechanism protecting LP principal from value leakage. When it uses the wrong mid-price formula, the drawdown floor it enforces is lower than the configured threshold. For a 10% oracle spread the floor is underestimated by ~0.052%; for a 50% spread by ~2%. Any `zeroForOne` swap that drains value into that gap proceeds without reverting, causing direct LP principal loss beyond the pool admin's configured drawdown limit. This satisfies the "direct loss of user principal" criterion for Medium severity.

## Likelihood Explanation

Every pool that deploys `OracleValueStopLossExtension` with a non-zero drawdown and a non-zero oracle spread is affected on every `zeroForOne` swap. No special role or privileged access is required; any public caller can execute the swap. The discrepancy is always present (not edge-case dependent) and grows monotonically with the oracle spread.

## Recommendation

Replace the arithmetic mean in `_afterSwapOracleStopLoss` with the same geometric mean used by the pool:

```solidity
// Before (line 218):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After:
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

Add the import for `SwapMath` to `OracleValueStopLossExtension.sol`. This makes the value metrics computed by the stop-loss consistent with the mid-price used for actual swap settlement, ensuring the drawdown floor is enforced at the correct threshold.

## Proof of Concept

**Setup:** Pool with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5%), oracle bid = `1.0 × 2^64`, ask = `1.1 × 2^64` (10% spread). Bin has t0 = 100, t1 = 100, totalShares = 100.

**Geometric mid** = `sqrt(1.0 × 1.1) × 2^64 ≈ 1.04881 × 2^64`  
**Arithmetic mid** = `1.05 × 2^64`

| | Geometric (correct) | Arithmetic (actual) |
|---|---|---|
| t1 contribution to metricT0 | `100 / 1.04881 ≈ 95.345` | `100 / 1.05 ≈ 95.238` |
| metricT0 (×1e6/share) | ≈ 1,953,450 | ≈ 1,952,380 |
| watermark hwm0 | 1,953,450 | 1,952,380 |
| floor (×0.95) | **1,855,778** | **1,854,761** |

A value-draining `zeroForOne` swap that reduces `metricT0` to **1,855,000** sits:
- **Above** the arithmetic floor (1,854,761) → `breach0 = false` → stop-loss **does not revert**
- **Below** the geometric floor (1,855,778) → stop-loss **should have reverted**

The swap settles, LP value leaks past the configured 5% drawdown limit, and the stop-loss watermark is updated to the new lower value, permanently resetting the protection baseline downward.

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L270-273)
```text
    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
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
