### Title
Arithmetic-Mean Mid Price in `OracleValueStopLossExtension` Understates Token0 Watermarks, Allowing LP Funds to Drain Beyond Configured Drawdown — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid price as an **arithmetic mean** of bid and ask, while the pool's swap engine (`SwapMath.midAndSpreadFeeX64FromBidAsk`) computes it as a **geometric mean**. This is the direct analog of the Timeswap sqrtDiscriminant bug: a mathematical formula inconsistency causes the stop-loss guard to evaluate LP bin value against a systematically different price than the pool uses for swap execution, miscalibrating the high watermarks and allowing LP funds to drain further than the configured drawdown threshold before the guard triggers.

---

### Finding Description

**Pool mid price (geometric mean):** [1](#0-0) 

```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
```

**Stop-loss guard mid price (arithmetic mean):** [2](#0-1) 

```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
```

By the AM-GM inequality, `(bid + ask)/2 ≥ sqrt(bid × ask)` always, with equality only when `bid == ask`. Whenever any spread exists, `mid_arith > mid_geo`.

This `midPriceX64` is then fed into `_metrics`: [3](#0-2) 

```solidity
metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

Because `mid_arith > mid_geo`:
- `metricT0` is **understated**: the `t1 * Q64 / midPriceX64` term is divided by a larger-than-correct price, producing a smaller token0-equivalent value for the token1 holdings.
- `metricT1` is **overstated**: the `t0 * midPriceX64 / Q64` term is multiplied by a larger-than-correct price.

The understated `metricT0` is then stored as the high watermark: [4](#0-3) 

```solidity
(uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
if (breach0 && zeroForOne) {
  revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
}
```

The floor is `hwm0 * (1 - drawdown)`. Because `hwm0` was set from an understated metric, the floor is lower than intended. A subsequent genuine value loss must fall further below this already-too-low floor before the stop-loss triggers, allowing LP funds to drain beyond the configured drawdown.

The `PriceVelocityGuardExtension` confirms the correct formula: it explicitly calls `SwapMath.midAndSpreadFeeX64FromBidAsk` (geometric mean) for its mid price: [5](#0-4) 

```solidity
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

The `OracleValueStopLossExtension` is the only extension that deviates from this pattern.

---

### Impact Explanation

The stop-loss guard's token0 watermarks are set systematically lower than the true geometric-mid value of the bin. The drawdown floor is therefore lower than the admin configured. For a pool with a 5% drawdown limit and a 10% oracle spread, the arithmetic mean exceeds the geometric mean by approximately `(spread/2)^2 / (2 × mid) ≈ 0.125%`, meaning the effective drawdown protection is ~5.125% instead of 5%. The error grows quadratically with spread width, becoming material during volatile markets when spreads widen. LP funds can drain beyond the intended threshold before the guard reverts the swap.

---

### Likelihood Explanation

Every public swap through a pool with `OracleValueStopLossExtension` configured and a non-zero drawdown triggers `_afterSwapOracleStopLoss`. The miscalibration is present on every call whenever `bid < ask` (i.e., always in normal operation). No privileged access or special setup is required; any trader executing a swap causes the watermark to be set at the wrong value.

---

### Recommendation

Replace the arithmetic mean with the geometric mean, consistent with the pool's own mid-price derivation and with `PriceVelocityGuardExtension`:

```solidity
// Before (wrong):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct):
uint256 midPriceX64 = Math.sqrt(uint256(bidPriceX64) * uint256(askPriceX64));
```

This ensures the stop-loss guard evaluates LP value at the same price the pool uses for swap execution, making the drawdown floor match the admin's configured intent.

---

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5%), and a price provider returning `bid = 0.95 × mid_geo`, `ask = 1.05 × mid_geo` (10% spread).
2. Add liquidity to bin 0 with `t0 = 1000`, `t1 = 1000`.
3. Execute a swap (`zeroForOne = false`) to initialize the watermark. The guard computes:
   - `mid_arith = (0.95 + 1.05)/2 × mid_geo = mid_geo` (coincidentally equal here for symmetric spread)
   - But for asymmetric spreads (e.g., `bid = 0.92 × mid_geo`, `ask = 1.05 × mid_geo`): `mid_arith = 0.985 × mid_geo`, `mid_geo_correct = sqrt(0.92 × 1.05) × mid_geo ≈ 0.9827 × mid_geo`. The arithmetic mean is ~0.23% higher, understating `metricT0` by ~0.23%.
4. Drain the bin by 5.23% of token0 value. The stop-loss should trigger at 5% but does not, because the floor was set 0.23% too low.
5. Confirm the stop-loss triggers only after the additional 0.23% drain, demonstrating LP funds leaked beyond the configured threshold.

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L70-71)
```text
    midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
    baseFeeX64 = Math.mulDiv(askPriceX64, ONE_X64, midPriceX64, Math.Rounding.Ceil) - ONE_X64;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L218-218)
```text
    uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L254-255)
```text
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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L48-48)
```text
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```
