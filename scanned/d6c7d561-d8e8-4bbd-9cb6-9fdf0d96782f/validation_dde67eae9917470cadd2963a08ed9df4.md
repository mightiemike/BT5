### Title
`OracleValueStopLossExtension` Uses Arithmetic Mean Instead of Geometric Mean for Mid-Price, Causing Systematic Stop-Loss Miscalibration — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid-price as the **arithmetic mean** of bid and ask, while every other component in the protocol (pool swap engine, data provider) uses the **geometric mean**. Because AM ≥ GM always, the stop-loss systematically overestimates `metricToken1` and underestimates `metricToken0`, making the guard less protective for `!zeroForOne` swaps (token1 in, token0 out) — exactly the direction that drains token0 from LPs.

---

### Finding Description

**Wrong value in the stop-loss metric computation**

In `_afterSwapOracleStopLoss`:

```solidity
// OracleValueStopLossExtension.sol line 218
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;  // arithmetic mean
``` [1](#0-0) 

Every other mid-price derivation in the codebase uses the geometric mean:

```solidity
// SwapMath.sol — used for actual swap pricing
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
``` [2](#0-1) 

```solidity
// MetricOmmPoolDataProvider.sol — used for depth/quote views
midPriceX64 = Math.sqrt(uint256(bidFromOracleX64) * uint256(askFromOracleX64));
``` [3](#0-2) 

The stop-loss metric formulas are:

```
metricToken0 = t0·SCALE/shares + (t1 · 2^64 / mid) · SCALE/shares
metricToken1 = (t0 · mid / 2^64) · SCALE/shares + t1·SCALE/shares
``` [4](#0-3) 

Because AM > GM (by AM-GM inequality), substituting AM for mid produces:

| Metric | Effect of AM > GM |
|---|---|
| `metricToken0` | **Underestimated** (`t1/mid` term shrinks) |
| `metricToken1` | **Overestimated** (`t0·mid` term grows) |

The stop-loss blocks `!zeroForOne` swaps when `metricToken1 < hwm1 · floorMultiplier / E6`:

```solidity
(uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
if (breach1 && !zeroForOne) {
    revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
}
``` [5](#0-4) 

Because `metricToken1` is overestimated, the breach condition `metricT1 < floor` is harder to satisfy. The stop-loss fails to trigger for `!zeroForOne` swaps that should have been blocked.

---

### Impact Explanation

The `OracleValueStopLossExtension` is the primary on-chain mechanism protecting LP capital from oracle-driven value leakage. When the stop-loss fails to trigger for `!zeroForOne` swaps (token1 in, token0 out), token0 continues to leave the pool at a price that the stop-loss was configured to block. LPs suffer direct loss of their token0 principal beyond the configured drawdown floor. The magnitude of the miscalibration scales with the oracle spread:

- 1% spread → AM exceeds GM by ~0.005% of mid
- 10% spread → AM exceeds GM by ~0.125% of mid
- 50% spread → AM exceeds GM by ~3.2% of mid

For pools with wider oracle spreads or tight drawdown floors, the systematic overestimation of `metricToken1` creates a persistent blind spot in the stop-loss guard.

---

### Likelihood Explanation

The error is present on every swap that touches a pool configured with `OracleValueStopLossExtension`. No special conditions are required — any public `swap` call with `!zeroForOne` direction on such a pool triggers the miscalibrated check. The attacker does not need to manipulate any admin settings; they simply need to time their swap to a moment when the true (GM-based) metric has breached the floor while the AM-based metric has not.

---

### Recommendation

Replace the arithmetic mean with the geometric mean, consistent with `SwapMath.midAndSpreadFeeX64FromBidAsk`:

```diff
- uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
+ uint256 midPriceX64 = Math.sqrt(uint256(bidPriceX64) * uint256(askPriceX64));
``` [6](#0-5) 

---

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension`, drawdown floor = 5% (`drawdownE6 = 50000`), and a 10% oracle spread (bid = 0.95·mid, ask = 1.05·mid).
2. Set a watermark for bin 0 at the current per-share value.
3. Execute a series of `!zeroForOne` swaps that drain token0 from the pool.
4. After each swap, compute `metricToken1` using both AM and GM:
   - AM-based (stop-loss): `metricToken1_AM = t0 · AM / 2^64 · SCALE/shares + t1·SCALE/shares`
   - GM-based (correct): `metricToken1_GM = t0 · GM / 2^64 · SCALE/shares + t1·SCALE/shares`
5. Observe that when `metricToken1_GM < hwm1 · 0.95` (floor breached by correct metric), `metricToken1_AM` remains above the floor because AM > GM by ~0.125% for a 10% spread.
6. The stop-loss does not revert; the swap executes; LP token0 is drained beyond the configured protection boundary.

The root cause is the same bug class as the external report: a wrong value (AM instead of GM) is substituted into a critical state-update/guard calculation, causing the guard to be systematically miscalibrated in a direction that harms the protected party (LPs).

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L275-278)
```text
    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
    }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L70-71)
```text
    midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
    baseFeeX64 = Math.mulDiv(askPriceX64, ONE_X64, midPriceX64, Math.Rounding.Ceil) - ONE_X64;
```

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L283-283)
```text
      midPriceX64 = Math.sqrt(uint256(bidFromOracleX64) * uint256(askFromOracleX64));
```
