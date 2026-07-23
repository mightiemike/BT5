### Title
Division-Before-Multiplication in `_metrics` Causes Precision Loss That Can Falsely Trigger Stop-Loss, Blocking Legitimate Swaps — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._metrics()` computes per-share bin value using two nested `Math.mulDiv` calls. The intermediate division truncates the cross-token component before the subsequent multiplication by `METRIC_SCALE`, causing the computed metric to be systematically underestimated. When the true metric sits just above the drawdown floor, the truncated value can fall below it, causing `_checkAndUpdateWatermarks` to revert with `OracleStopLossTriggered` on a legitimate swap.

---

### Finding Description

In `_metrics` (lines 254–255 of `OracleValueStopLossExtension.sol`):

```solidity
metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

The inner `Math.mulDiv` performs a division first:

- `Math.mulDiv(t1, Q64, midPriceX64)` → `floor(t1 × 2^64 / midPriceX64)` (token1 converted to token0 units)
- `Math.mulDiv(t0, midPriceX64, Q64)` → `floor(t0 × midPriceX64 / 2^64)` (token0 converted to token1 units)

The fractional part discarded by each inner division is then multiplied by `METRIC_SCALE / shares` in the outer call — but that multiplication never happens because the fractional part was already lost. The correct computation is:

```
t1 × Q64 × METRIC_SCALE / (midPriceX64 × shares)
```

but the code computes:

```
floor(t1 × Q64 / midPriceX64) × METRIC_SCALE / shares
```

The maximum absolute error per metric is `METRIC_SCALE / shares` (up to `1e6` when `shares = minimalMintableLiquidity = 1`).

This underestimated metric is then compared against the drawdown floor in `_applyWatermark`:

```solidity
breached = metric < (hwm * floorMultiplier) / E6;
```

If the computed metric falls below `hwm × (1 − drawdown)` while the true metric does not, `_checkAndUpdateWatermarks` reverts with `OracleStopLossTriggered`, and the entire swap reverts.

---

### Impact Explanation

The stop-loss guard fires on a legitimate swap. The pool's `afterSwap` hook reverts, causing the swap transaction to revert. Any swap direction guarded by the falsely-breached metric (`zeroForOne` for `metricT0`, `!zeroForOne` for `metricT1`) becomes permanently blocked until the watermark decays below the computed (underestimated) metric or an admin resets the watermark. This renders the pool's swap functionality unusable for affected directions, constituting broken core pool functionality.

---

### Likelihood Explanation

The false positive requires the true per-share metric to lie in the window `[hwm × (1 − drawdown), hwm × (1 − drawdown) + METRIC_SCALE/shares)`. This window is widest (up to `1e6` units wide) when `shares` is at its minimum (`minimalMintableLiquidity`). Pools configured with tight drawdowns (e.g., 1–5%) and low minimum liquidity are most exposed. The condition is reachable by any unprivileged user who calls `swap` when the bin's true metric is near the floor — no special setup is required beyond normal pool operation.

---

### Recommendation

Delay all divisions to the final step. Replace the nested `mulDiv` with a single three-argument call that keeps the full numerator precision:

```solidity
// metricT0 cross-token component: t1 × Q64 × METRIC_SCALE / (midPriceX64 × shares)
// Use mulDiv(t1 × METRIC_SCALE, Q64, midPriceX64 × shares) — verify denominator fits uint256
// Or equivalently:
uint256 t1InT0Scaled = Math.mulDiv(uint256(t1), Q64 * METRIC_SCALE, midPriceX64);
metricT0 = _clampMetric(t0ps + t1InT0Scaled / shares);

uint256 t0InT1Scaled = Math.mulDiv(uint256(t0), midPriceX64 * METRIC_SCALE, Q64);
metricT1 = _clampMetric(t0InT1Scaled / shares + t1ps);
```

`Q64 * METRIC_SCALE = 2^64 × 1e6 ≈ 1.8e25` fits comfortably in `uint256`, and `t1 × Q64 × METRIC_SCALE ≤ 2^104 × 1.8e25 < 2^256`, so no overflow occurs in the numerator. The denominator `midPriceX64 × shares` must be checked for overflow; if it can exceed `uint256`, use `Math.mulDiv(t1InT0Scaled, 1, shares)` as a two-step fallback.

---

### Proof of Concept

**Setup:**
- Pool with `minimalMintableLiquidity = 1`, `drawdownE6 = 100_000` (10%).
- Bin 0 has `t0 = 0`, `t1 = 1` (1 scaled unit of token1), `totalShares = 1`.
- `midPriceX64 = 3000 × 2^64` (price = 3000 token1 per token0, e.g. ETH/USDC).
- Admin sets watermark `hwm0 = 400` (token0-denominated per-share metric).

**Metric computation (current code):**
```
inner = Math.mulDiv(1, 2^64, 3000 × 2^64) = floor(1/3000) = 0
metricT0 = t0ps + Math.mulDiv(0, 1e6, 1) = 0 + 0 = 0
```

**True metric:**
```
true_metricT0 = 1 × 2^64 × 1e6 / (3000 × 2^64 × 1) = 1e6 / 3000 ≈ 333
```

**Stop-loss check:**
```
floor = hwm0 × (1e6 − 100_000) / 1e6 = 400 × 0.9 = 360
computed metricT0 = 0 < 360  →  OracleStopLossTriggered fires
true metricT0     = 333 < 360 → also a breach in this example
```

Adjust to `hwm0 = 300` (so floor = 270) and `t1 = 1` at price 3000:
```
true_metricT0 ≈ 333 > 270  →  no breach (correct)
computed metricT0 = 0 < 270  →  OracleStopLossTriggered fires (false positive)
```

Any `zeroForOne` swap on this pool reverts. The pool's sell direction is permanently bricked until the admin intervenes. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L326-336)
```text
  /// @dev Ratchet up on new highs; report breach below the drawdown floor. Direction-aware
  ///      blocking is decided by the caller.
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
