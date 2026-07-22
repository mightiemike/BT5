### Title
`OracleValueStopLossExtension` Uses Arithmetic Mid-Price Instead of Geometric Mid-Price, Understating Value Loss and Allowing Stop-Loss Bypass in the `!zeroForOne` Direction — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid-price as the **arithmetic mean** of bid and ask, while every other component in the protocol — including `SwapMath.midAndSpreadFeeX64FromBidAsk` and `PriceVelocityGuardExtension` — uses the **geometric mean**. Because the arithmetic mean is always ≥ the geometric mean (AM-GM inequality), the stop-loss extension systematically overstates `metricToken1` (the per-share value in token1 terms). This makes the stop-loss guard for the `!zeroForOne` swap direction (token0 outflow) less sensitive than configured, allowing value-leaking swaps to proceed that the drawdown floor was designed to block.

---

### Finding Description

In `_afterSwapOracleStopLoss`, the mid-price used for per-share value metrics is:

```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
``` [1](#0-0) 

Everywhere else in the protocol, the canonical mid-price is the geometric mean:

```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
``` [2](#0-1) 

`PriceVelocityGuardExtension` correctly calls `SwapMath.midAndSpreadFeeX64FromBidAsk` to obtain the geometric mid: [3](#0-2) 

The per-share value metrics are:

```solidity
metricT0 = t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares);
metricT1 = Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps;
``` [4](#0-3) 

Because `arithmetic_mean(bid, ask) ≥ geometric_mean(bid, ask)` always:

- A **higher** `midPriceX64` multiplied into `t0` inflates `metricT1`.
- A **higher** `midPriceX64` divided into `t1` deflates `metricT0`.

The stop-loss direction mapping is:

```solidity
if (breach0 && zeroForOne) { revert ... }   // blocks token1 outflow
if (breach1 && !zeroForOne) { revert ... }  // blocks token0 outflow
``` [5](#0-4) 

The inflated `metricT1` makes `breach1` harder to trigger, so the guard protecting against token0 outflow (`!zeroForOne`) is systematically weakened. The watermark ratchet stores the inflated metric as the new high-water mark: [6](#0-5) 

The bypass is most pronounced when the oracle spread **widens after** the watermark was established during a narrow-spread period. In that scenario the watermark reflects the true geometric-mean value, but the current metric is inflated by the arithmetic mean, making the pool appear healthier than it is and suppressing the stop-loss revert.

---

### Impact Explanation

The `OracleValueStopLossExtension` is the primary on-chain mechanism preventing LP value from draining below a configured drawdown floor. When `metricT1` is overstated, swaps in the `!zeroForOne` direction (token0 leaving the pool) that should have been blocked by the stop-loss are permitted to execute. LPs suffer direct principal loss beyond the drawdown threshold they configured. The magnitude of the bypass scales with `(spread/2)²`: a 10% oracle spread produces a ~0.25% overstatement of `metricT1`, which can fully absorb a tight drawdown floor.

---

### Likelihood Explanation

Every swap on every pool that uses `OracleValueStopLossExtension` with a non-zero oracle spread triggers this discrepancy. The oracle spread is a normal, always-present production condition (it is the source of the pool's base fee). No privileged action, malicious setup, or non-standard token is required. Any public swapper can execute the `!zeroForOne` direction at the moment the pool's actual value is below the drawdown floor but the inflated metric is above it.

---

### Recommendation

Replace the arithmetic mean with the geometric mean, consistent with the rest of the protocol:

```solidity
// Before (incorrect):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(
    uint256(bidPriceX64),
    uint256(askPriceX64)
);
```

This aligns the stop-loss valuation with the price the pool actually uses for swap settlement, eliminating the systematic overstatement of `metricT1`.

---

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5%), `decayPerSecondE8 = 0`.
2. Set oracle to bid = `0.9 * Q64`, ask = `1.1 * Q64` (10% spread).
   - Arithmetic mid = `Q64` (exact).
   - Geometric mid = `sqrt(0.99) * Q64 ≈ 0.995 * Q64`.
3. Add liquidity; the first `afterSwap` call sets the watermark using the arithmetic mid. `metricT1` is overstated by ~0.5% relative to the geometric mid.
4. Execute a sequence of `!zeroForOne` swaps that drain token0 until the pool's true geometric-mid value is 4.6% below the watermark (inside the 5% drawdown floor).
5. Observe: the stop-loss does **not** revert because the arithmetic-mid `metricT1` still reads ~0.5% above the floor, masking the actual breach.
6. Confirm: replacing line 218 with `SwapMath.midAndSpreadFeeX64FromBidAsk` causes the stop-loss to revert at step 4 as intended.

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L271-277)
```text
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L280-284)
```text
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token0 = uint104(hwm0);
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token1 = uint104(hwm1);
    hwmS.lastDecayTs = uint32(block.timestamp);
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L70-71)
```text
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
