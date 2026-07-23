### Title
`OracleValueStopLossExtension` Uses Arithmetic Mid Instead of Geometric Mid, Miscalibrating the LP Value Guard - (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid price as the **arithmetic mean** of bid and ask, while every other component in the protocol — the pool's swap engine and `PriceVelocityGuardExtension` — uses the **geometric mean** (`sqrt(bid * ask)`). By AM-GM inequality the arithmetic mean is always ≥ the geometric mean, so the stop-loss guard evaluates LP value at a systematically inflated price. This makes the guard under-protective in the `zeroForOne = true` direction: token1 can drain from the pool beyond the configured drawdown threshold without triggering the stop-loss.

---

### Finding Description

In `_afterSwapOracleStopLoss`, the mid price is computed as:

```solidity
// OracleValueStopLossExtension.sol line 218
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
``` [1](#0-0) 

The pool's canonical mid-price function, `SwapMath.midAndSpreadFeeX64FromBidAsk`, uses the geometric mean:

```solidity
// SwapMath.sol line 70
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
``` [2](#0-1) 

`PriceVelocityGuardExtension.beforeSwap` also calls `SwapMath.midAndSpreadFeeX64FromBidAsk` for its own mid-price computation, confirming the geometric mean is the protocol-wide standard: [3](#0-2) 

The two per-share value metrics are:

```
metricToken0 = t0*SCALE/shares + (t1 * Q64 / mid) * SCALE / shares
metricToken1 = (t0 * mid / Q64) * SCALE / shares + t1*SCALE/shares
``` [4](#0-3) 

Because `arithmeticMid ≥ geometricMid` (AM-GM), the inflated mid has opposite effects on the two metrics:

| Metric | Effect of inflated mid | Guard consequence |
|---|---|---|
| `metricToken0` | `t1 * Q64 / mid` is **smaller** → metric **understated** | Watermark set too low; breach threshold too low; guard for `zeroForOne=true` (token1 outflow) is **less sensitive** |
| `metricToken1` | `t0 * mid / Q64` is **larger** → metric **overstated** | Watermark set too high; breach threshold too high; guard for `zeroForOne=false` (token0 outflow) is **more sensitive** |

The `_checkAndUpdateWatermarks` function then compares these miscalculated metrics against miscalculated watermarks and emits a breach only when `metric < hwm * floorMultiplier / E6`: [5](#0-4) 

---

### Impact Explanation

The primary fund-impacting consequence is in the `zeroForOne = true` direction (token0 in, token1 out). The contract's own NatSpec states:

> `metricToken0 breach (mid suspect-high) blocks zeroForOne == true (token1 outflow)` [6](#0-5) 

Because the arithmetic mid is always ≥ the geometric mid, `metricToken0` is always understated. The watermark is therefore set lower than it should be, and the drawdown floor is lower than intended. Swaps that drain token1 past the configured drawdown threshold are not blocked. LPs lose principal beyond the protection they configured.

The magnitude of the error scales with the oracle spread: for a 10% bid/ask spread the arithmetic mid exceeds the geometric mid by ~0.125%; for a 50% spread the error reaches ~3.2%. Pools with wider spreads (e.g., volatile assets, low-liquidity markets) suffer proportionally larger under-protection.

---

### Likelihood Explanation

Every pool that deploys `OracleValueStopLossExtension` with a non-zero `drawdownE6` is affected on every swap that traverses a bin. No special attacker action is required — the miscalculation is structural and fires on every normal swap. Any user who swaps `zeroForOne = true` benefits from the weakened guard without any privileged access.

---

### Recommendation

Replace the arithmetic mean with the geometric mean, consistent with the rest of the protocol:

```solidity
// Before (incorrect):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

This matches the formula used by `PriceVelocityGuardExtension` and the pool's own swap engine, ensuring the stop-loss evaluates LP value at the same price the pool uses for execution.

---

### Proof of Concept

Given `bid = 0.9 * Q64`, `ask = 1.1 * Q64` (≈10% spread), `t0 = t1 = 1000`, `shares = 1000`, `drawdownE6 = 50_000` (5%):

- **Geometric mid** (correct): `sqrt(0.9 * 1.1) * Q64 = sqrt(0.99) * Q64 ≈ 0.99499 * Q64`
- **Arithmetic mid** (used): `(0.9 + 1.1)/2 * Q64 = 1.0 * Q64`

`metricToken0` at geometric mid:
```
t0ps + t1 * Q64 / geometricMid / shares * SCALE
= 1000 + 1000 * Q64 / (0.99499 * Q64) * SCALE / 1000
= 1000 + 1005.03 ≈ 2005
```

`metricToken0` at arithmetic mid (what the contract computes):
```
= 1000 + 1000 * Q64 / (1.0 * Q64) * SCALE / 1000
= 1000 + 1000 = 2000
```

The watermark is set to 2000 instead of 2005. The drawdown floor is `2000 * 0.95 = 1900` instead of `2005 * 0.95 = 1904.75`. A subsequent swap that reduces `metricToken0` to 1902 would correctly trigger the guard at the geometric watermark (1902 < 1904.75) but passes silently at the arithmetic watermark (1902 > 1900), allowing token1 to drain past the LP's configured protection.

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L22-25)
```text
///      A pure mid move pushes the metrics in opposite directions; a value leak pushes both down.
///        - metricToken0 breach (mid suspect-high) blocks zeroForOne == true  (token1 outflow)
///        - metricToken1 breach (mid suspect-low)  blocks zeroForOne == false (token0 outflow)
///        - both breached blocks both directions
```

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L267-284)
```text
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
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L64-71)
```text
  /// @notice Geometric mid price (Q64.64) and spread fee in Q64.64 from bid/ask oracle quotes.
  function midAndSpreadFeeX64FromBidAsk(uint256 bidPriceX64, uint256 askPriceX64)
    internal
    pure
    returns (uint256 midPriceX64, uint256 baseFeeX64)
  {
    midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
    baseFeeX64 = Math.mulDiv(askPriceX64, ONE_X64, midPriceX64, Math.Rounding.Ceil) - ONE_X64;
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L48-51)
```text
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    // casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128 (same bound as pool)
    // forge-lint: disable-next-line(unsafe-typecast)
    uint128 midPrice = uint128(midPriceX64);
```
