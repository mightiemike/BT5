### Title
Arithmetic Mean Used Instead of Geometric Mean for Mid Price in `OracleValueStopLossExtension` Allows Stop-Loss Guard Bypass During Wide Oracle Spreads — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid price as an arithmetic mean of bid and ask, while the pool's swap engine always uses the geometric mean. By the AM-GM inequality, the arithmetic mean is always ≥ the geometric mean. This systematic inflation of the mid price used by the stop-loss guard causes `metricT1` (token1-denominated value per share) to be overstated whenever the oracle spread is non-trivial. When the watermark was set during a tight-spread period and value is extracted during a wide-spread period, the inflated current `metricT1` can remain above the drawdown floor of the accurate watermark, causing the guard to silently pass a swap that should have been reverted.

---

### Finding Description

In `_afterSwapOracleStopLoss`, line 218:

```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
``` [1](#0-0) 

The pool's swap engine, by contrast, always derives mid price as the geometric mean:

```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
``` [2](#0-1) 

This geometric mean is used in every real swap execution path: [3](#0-2) 

The `_metrics` function feeds `midPriceX64` into both value metrics:

```solidity
metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
``` [4](#0-3) 

Because AM ≥ GM always:
- `metricT0` (divides by mid) is **understated** by the stop-loss
- `metricT1` (multiplies by mid) is **overstated** by the stop-loss

The watermark ratchet stores the maximum observed metric:

```solidity
if (metric >= hwm) return (metric, false);
breached = metric < (hwm * floorMultiplier) / E6;
``` [5](#0-4) 

When the watermark was set during a tight-spread period (AM ≈ GM, accurate watermark W), and a subsequent swap occurs during a wide-spread period, the inflated AM mid inflates the current `metricT1` above what the geometric mid would produce. This inflated current metric can remain above the drawdown floor of the accurate watermark W, so `breached` stays `false` and the swap is not reverted — even though the geometric-mid metric would have fallen below the floor.

---

### Impact Explanation

**Concrete scenario (50% oracle spread, 5% drawdown):**

| | Tight spread (watermark set) | Wide spread (extraction) |
|---|---|---|
| bid/ask | 0.99 / 1.01 | 0.75 / 1.25 |
| AM mid | ≈ 1.0 | ≈ 1.0 |
| GM mid | ≈ 0.99995 | ≈ 0.96825 |
| t0, t1, shares | 1000, 1000, 1000 | 950, 950, 1000 (5% extracted) |
| metricT1 (AM) | ≈ 2000 → watermark W=2000 | ≈ 1900 |
| metricT1 (GM) | ≈ 2000 → watermark W=2000 | ≈ 1870 |
| Floor (5% drawdown) | — | 1900 |
| Guard triggers? | — | AM: 1900 < 1900 → **NO** / GM: 1870 < 1900 → **YES** |

The attacker extracts 5% of LP principal. The stop-loss guard, which was configured precisely to block 5% drawdown, silently passes the swap because the inflated AM mid makes `metricT1` appear equal to the floor rather than below it. The guard that should have reverted the swap does not.

The magnitude of the bypass scales with spread width. For a 50% spread the AM-GM gap is ~3.3%, meaning the guard can fail to detect value extraction up to that magnitude beyond the configured drawdown threshold. For typical 5–10% spreads the gap is ~0.06–0.25%, still non-zero and exploitable in combination with a tight drawdown setting.

The `metricT1` breach blocks `zeroForOne == false` (token0 outflow). The bypass therefore allows an attacker to drain token0 from the pool during wide-spread periods without triggering the stop-loss. [6](#0-5) 

---

### Likelihood Explanation

Every pool that uses `OracleValueStopLossExtension` with a non-zero drawdown is affected whenever the oracle spread is non-trivial. Oracle spreads widen naturally during high volatility — precisely the conditions under which the stop-loss is most needed. No privileged access is required; any user can submit a swap. The bypass is passive: the attacker simply executes a value-extracting swap during a wide-spread window and the guard fails to revert it.

---

### Recommendation

Replace the arithmetic mean with the geometric mean, consistent with the pool's own mid-price derivation:

```solidity
// Before (incorrect):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(
    uint256(bidPriceX64),
    uint256(askPriceX64)
);
```

This ensures the stop-loss evaluates LP value at the same mid price the pool uses for swap execution, eliminating the systematic bias and making the drawdown threshold accurate regardless of spread width.

---

### Proof of Concept

```solidity
// Scenario: watermark set at tight spread, extraction at wide spread
// Pool: t0=1000, t1=1000, shares=1000, METRIC_SCALE=1e6, Q64=2^64

// Step 1: First swap — tight spread (bid≈0.99, ask≈1.01)
// AM mid ≈ GM mid ≈ 1.0 * Q64
// metricT1 = (1000 * Q64 / Q64 + 1000) * 1e6 / 1000 = 2000
// Watermark W = 2000, drawdown=5% → floor = 1900

// Step 2: Oracle spread widens to 50% (bid=0.75*Q64, ask=1.25*Q64)
// AM mid = (0.75 + 1.25)/2 * Q64 = 1.0 * Q64
// GM mid = sqrt(0.75 * 1.25) * Q64 = 0.96825 * Q64

// Step 3: Attacker extracts 5% value → t0=950, t1=950
// metricT1 with AM mid = (950 * 1.0 + 950) * 1e6 / 1000 = 1900
// metricT1 with GM mid = (950 * 0.96825 + 950) * 1e6 / 1000 ≈ 1870

// Guard check: breached = metricT1 < floor
// AM: 1900 < 1900 → false → swap NOT reverted (bypass)
// GM: 1870 < 1900 → true  → swap REVERTED (correct behavior)

// Result: 5% of LP principal extracted without stop-loss triggering.
// The guard configured for exactly 5% drawdown protection provides zero protection
// during a 50% oracle spread window.
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L218-218)
```text
    uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L254-255)
```text
    metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L275-277)
```text
    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L333-334)
```text
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L70-70)
```text
    midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L333-333)
```text
    (uint256 midPriceX64, uint256 baseFeeX64) = SwapMath.midAndSpreadFeeX64FromBidAsk(bidPriceX64, askPriceX64);
```
