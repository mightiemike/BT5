### Title
Arithmetic Mean Used Instead of Geometric Mean for Mid Price in `OracleValueStopLossExtension` Systematically Underestimates `metricT0`, Weakening Stop-Loss Guard for the `zeroForOne` Direction — (File: `metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid price as the **arithmetic mean** of bid and ask prices. Every other mid-price consumer in the protocol — `SwapMath.midAndSpreadFeeX64FromBidAsk` (used by the pool for all swap settlement) and `PriceVelocityGuardExtension.beforeSwap` — uses the **geometric mean** (`sqrt(bid * ask)`). By AM-GM inequality the arithmetic mean is always ≥ the geometric mean, so the stop-loss extension inflates `midPriceX64` relative to the value the pool actually traded at. This causes `metricT0` (value-per-share in token0 terms) to be systematically underestimated, setting the high-watermark and its drawdown floor lower than they should be, and allowing more token1 to drain from the pool before the stop-loss triggers.

---

### Finding Description

**Wrong mid-price in the stop-loss guard:** [1](#0-0) 

```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;  // arithmetic mean
```

**Canonical geometric mean used everywhere else:** [2](#0-1) 

```solidity
/// @notice Geometric mid price (Q64.64) and spread fee in Q64.64 from bid/ask oracle quotes.
function midAndSpreadFeeX64FromBidAsk(...) {
    midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);   // geometric mean
    ...
}
```

**`PriceVelocityGuardExtension` uses the geometric mean correctly:** [3](#0-2) 

**The inflated mid price feeds directly into `_metrics`:** [4](#0-3) 

```solidity
metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

With `midPriceX64_arith > midPriceX64_geom`:
- `t1 * Q64 / midPriceX64` is **smaller** → `metricT0` is **underestimated**
- `t0 * midPriceX64 / Q64` is **larger** → `metricT1` is **overestimated**

**The watermark ratchet locks in the underestimated value:** [5](#0-4) 

`_applyWatermark` ratchets the watermark up to `metric` on new highs. Because `metricT0` is underestimated from the first swap, `hwmS.token0` is set lower than the true per-share value. The drawdown floor `hwm * floorMultiplier / E6` is therefore also lower, allowing the pool to lose more token1 than the configured `drawdownE6` before the stop-loss fires.

**Quantified error:** For oracle spread `s` (i.e., `ask = mid*(1+s/2)`, `bid = mid*(1-s/2)`):

```
arithmetic_mid / geometric_mid = 1 / sqrt(1 - s²/4) ≈ 1 + s²/8
```

| Oracle spread | Arithmetic inflation | Effective extra drawdown allowed |
|---|---|---|
| 10% | ~0.125% | ~0.125% of watermark |
| 20% | ~0.5% | ~0.5% of watermark |
| 50% | ~3.2% | ~3.2% of watermark |

---

### Impact Explanation

The stop-loss guard for the `zeroForOne` direction (token0 in → token1 out) is weakened by the oracle spread. An attacker can drain token1 from the pool beyond the configured `drawdownE6` threshold before the stop-loss reverts the swap. For pools with wide oracle spreads (e.g., 20–50%), the effective protection is materially weaker than what the pool admin configured. This is a direct loss of LP principal that the stop-loss was designed to prevent.

---

### Likelihood Explanation

The bug is unconditionally present on every swap through any pool that has `OracleValueStopLossExtension` attached and a non-zero oracle spread. No special setup is required: any public trader executing a `zeroForOne` swap triggers the miscalculated watermark update. The error grows with oracle spread, so pools using wider-spread oracles (e.g., volatile assets) are most exposed.

---

### Recommendation

Replace the arithmetic mean with the protocol-canonical geometric mean, consistent with `SwapMath.midAndSpreadFeeX64FromBidAsk` and `PriceVelocityGuardExtension`:

```solidity
// Before (line 218):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After:
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

This ensures the stop-loss metrics are computed at the same price the pool used for swap settlement.

---

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5% drawdown allowed).
2. Configure the oracle to return `bid = 0.8 * Q64`, `ask = 1.2 * Q64` (20% spread).
   - Arithmetic mid = `1.0 * Q64`
   - Geometric mid = `sqrt(0.96) * Q64 ≈ 0.9798 * Q64`
3. Seed the bin with `t0 = 1000`, `t1 = 1000`, `shares = 1000`.
4. Execute a non-`zeroForOne` swap to initialize the watermark. The extension computes:
   - `metricT0_arith = (1000 + 1000*Q64/(1.0*Q64)) * SCALE/1000 = 2000 * SCALE/1000 = 2*SCALE`
   - `metricT0_geom  = (1000 + 1000*Q64/(0.9798*Q64)) * SCALE/1000 ≈ 2020.6 * SCALE/1000`
   - Watermark set to `2*SCALE` instead of the correct `≈2020.6*SCALE/1000`.
5. Drain token1 via `zeroForOne` swaps until `metricT0` falls to `2*SCALE * 0.95 = 1.9*SCALE`.
   - Correct floor would have been `2020.6 * 0.95 ≈ 1919.6` (in SCALE units), i.e., the stop-loss should have fired earlier.
   - The attacker extracts ~2% more token1 than the 5% drawdown cap permits before the stop-loss triggers. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L270-284)
```text
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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L48-51)
```text
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    // casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128 (same bound as pool)
    // forge-lint: disable-next-line(unsafe-typecast)
    uint128 midPrice = uint128(midPriceX64);
```
