### Title
`OracleValueStopLossExtension` uses arithmetic mid-price instead of the pool's geometric mid-price, causing the stop-loss guard to compute miscalibrated per-share metrics — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` derives the oracle mid-price with an arithmetic mean `(bid + ask) / 2`, while every other component in the protocol — the pool's swap math, `PriceVelocityGuardExtension`, and the data-provider lens — derives it with the geometric mean `sqrt(bid * ask)` via `SwapMath.midAndSpreadFeeX64FromBidAsk`. Because the arithmetic mean is always ≥ the geometric mean (AM-GM), the stop-loss evaluates per-share metrics against a systematically inflated mid-price. For the `metricToken1` direction this inflates the apparent value per share, causing the guard to fail to trigger when it should and allowing value-leaking `!zeroForOne` swaps to proceed.

---

### Finding Description

**Root cause — two different mid-price formulas in the same hook path:**

`SwapMath.midAndSpreadFeeX64FromBidAsk` (the canonical formula used by the pool and by `PriceVelocityGuardExtension`):

```solidity
// metric-core/contracts/libraries/SwapMath.sol line 70
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);   // geometric mean
``` [1](#0-0) 

`PriceVelocityGuardExtension.beforeSwap` (correct — uses the same formula as the pool):

```solidity
// metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol line 48
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
``` [2](#0-1) 

`OracleValueStopLossExtension._afterSwapOracleStopLoss` (incorrect — uses arithmetic mean):

```solidity
// metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol line 218
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;  // arithmetic mean
``` [3](#0-2) 

This `midPriceX64` is then fed directly into `_metrics`, which computes both per-share metrics:

```solidity
metricT0 = t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares);
metricT1 = Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps;
``` [4](#0-3) 

**Effect of the discrepancy:**

By AM-GM: `(bid + ask)/2 ≥ sqrt(bid * ask)`, with equality only when `bid == ask`.

Let `δ = arithmetic_mid / geometric_mid − 1 > 0`. Then:

| Metric | Effect of inflated mid | Stop-loss consequence |
|---|---|---|
| `metricToken0` (∝ `t1/mid`) | **decreases** by factor `1/(1+δ)` | Guard triggers *earlier* for `zeroForOne` (false positive) |
| `metricToken1` (∝ `t0*mid`) | **increases** by factor `(1+δ)` | Guard triggers *later* for `!zeroForOne` (**false negative**) |

The false-negative case is fund-impacting: when the pool is losing value in token0 terms (mid is genuinely low, token0 is being drained by `!zeroForOne` swaps), the stop-loss should block further `!zeroForOne` swaps. Instead, the inflated arithmetic mid makes `metricToken1` appear higher than it actually is, so the guard does not revert. [5](#0-4) 

**Quantitative example:**

For a 50% oracle spread (bid = 0.75, ask = 1.25, both in the same unit):
- Geometric mid = `sqrt(0.75 × 1.25)` ≈ 0.9683
- Arithmetic mid = `(0.75 + 1.25)/2` = 1.0
- Inflation δ ≈ **+3.3%**

A pool configured with a 5% drawdown floor (`drawdownE6 = 50_000`) would need `metricToken1` to fall to 95% of its watermark before the guard fires. With a 3.3% inflation in the computed metric, the guard effectively requires a **8.3% actual drawdown** before it triggers — nearly double the configured threshold.

---

### Impact Explanation

The `OracleValueStopLossExtension` is the only on-chain mechanism protecting LP positions from sustained value leakage after adverse price moves. When the guard's `metricToken1` is inflated by the arithmetic-vs-geometric discrepancy, `!zeroForOne` swaps that should be blocked are permitted to continue. LP token0 balances are drained beyond the configured drawdown floor, constituting a direct loss of LP principal. The magnitude scales with the oracle spread: pools on volatile assets with wide bid/ask quotes (10–50%) face a 0.1–3.3% effective threshold relaxation, which can be material relative to a 5% drawdown configuration.

---

### Likelihood Explanation

The trigger is any public `swap` call on a pool that has `OracleValueStopLossExtension` configured with a non-zero `drawdownE6` and a non-trivial oracle spread. No privileged access is required. The attacker simply executes `!zeroForOne` swaps at a moment when the pool's value per share has fallen to the configured floor (as measured by the correct geometric mid), knowing the guard will not fire because it uses the inflated arithmetic mid. The wider the oracle spread, the larger the effective bypass.

---

### Recommendation

Replace the arithmetic mean in `_afterSwapOracleStopLoss` with the same geometric mean formula used by the pool and by `PriceVelocityGuardExtension`:

```solidity
// Before (incorrect):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct — matches pool swap math):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(
    uint256(bidPriceX64),
    uint256(askPriceX64)
);
```

This aligns the stop-loss metric computation with the price the pool actually uses for swap settlement, ensuring the configured `drawdownE6` floor is enforced at the correct threshold regardless of oracle spread width.

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_stopLoss_arithmeticMidBypass() public {
    // Pool with 50% oracle spread: bid=0.75, ask=1.25 (geometric mid ≈ 0.9683)
    uint128 bid = uint128(3 * Q64 / 4);   // 0.75 in Q64
    uint128 ask = uint128(5 * Q64 / 4);   // 1.25 in Q64

    // Geometric mid (what the pool uses):
    uint256 geoMid = Math.sqrt(uint256(bid) * uint256(ask)); // ≈ 0.9683 * Q64

    // Arithmetic mid (what the stop-loss uses):
    uint256 arithMid = (uint256(bid) + uint256(ask)) / 2;    // = 1.0 * Q64

    // With drawdownE6 = 50_000 (5%), the stop-loss floor for metricToken1 is:
    //   floor_geo   = watermark * geoMid / Q64 * 0.95   (correct)
    //   floor_arith = watermark * arithMid / Q64 * 0.95 (inflated by ~3.3%)
    //
    // A pool whose actual metricToken1 (at geometric mid) has fallen to 96% of
    // the watermark should trigger the guard — but the stop-loss sees it at
    // 96% * (arithMid/geoMid) ≈ 99.2% of the watermark and does NOT revert.
    //
    // Execute !zeroForOne swaps; assert OracleStopLossTriggered is NOT emitted
    // even though the geometric-mid metric is below the 95% floor.
}
```

The discrepancy is confirmed by comparing `OracleValueStopLossExtension` line 218 (arithmetic mean) against `SwapMath.midAndSpreadFeeX64FromBidAsk` line 70 (geometric mean via `Math.sqrt`) and `PriceVelocityGuardExtension` line 48 (which correctly calls `SwapMath.midAndSpreadFeeX64FromBidAsk`). [6](#0-5) [1](#0-0) [7](#0-6)

### Citations

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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L46-51)
```text
  ) external override returns (bytes4) {
    address pool_ = msg.sender;
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    // casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128 (same bound as pool)
    // forge-lint: disable-next-line(unsafe-typecast)
    uint128 midPrice = uint128(midPriceX64);
```

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
