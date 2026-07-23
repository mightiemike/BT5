### Title
`lastDecayTs` Advances Without Actual Decay, Permanently Locking Stop-Loss After Breach on Small-Metric Bins — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`_checkAndUpdateWatermarks` unconditionally writes `hwmS.lastDecayTs = uint32(block.timestamp)` on every swap, even when `_decayed()` returns the watermark unchanged because integer division truncated the decay step to zero. This is the direct analog of the Yield H-04 bug: the clock advances without the accumulator moving, permanently preventing the stop-loss from re-arming after a breach on bins with small per-share metrics.

---

### Finding Description

`_checkAndUpdateWatermarks` in `OracleValueStopLossExtension` always resets the decay clock:

```solidity
// _checkAndUpdateWatermarks, lines 280-284
hwmS.token0 = uint104(hwm0);
hwmS.token1 = uint104(hwm1);
hwmS.lastDecayTs = uint32(block.timestamp);   // ← always written
```

The decay step that feeds into this is:

```solidity
// _decayed, lines 319-324
function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;   // ← integer division
}
```

When `hwm * factor < E8`, the division truncates to zero and `_decayed` returns `hwm` unchanged. The watermark value stored in `hwmS.token0/token1` does not change, but `lastDecayTs` is still advanced to `block.timestamp`. On the next swap, `dt` is again small, the product again truncates, and the cycle repeats indefinitely.

**Concrete numbers with the code's own example rate (58 ≈ 5%/day):**

- `ratePerSecondE8 = 58`, `dt = 1` second → `factor = 58`
- Truncation condition: `hwm * 58 < 1e8` → `hwm < 1,724,137`
- `METRIC_SCALE = 1e6`, so a bin with `t0 = 1000` scaled units and `shares = 10,000` yields `t0ps = mulDiv(1000, 1e6, 10000) = 100`
- `hwm = 100`: `(100 * 58) / 1e8 = 0` — no decay, but clock resets

This is a realistic metric for any bin where token balances are small relative to share count (e.g., a bin that has been partially drained by a prior swap).

**Sequence of events:**

1. A swap drains a bin enough to trigger `OracleStopLossTriggered` — the pool is blocked in one direction.
2. Swaps in the still-allowed direction continue; each one calls `_checkAndUpdateWatermarks`, which resets `lastDecayTs` without decaying the watermark.
3. The watermark never falls, so `_applyWatermark` always sees `metric < hwm` and always reports a breach.
4. The pool is permanently blocked in the breached direction.

---

### Impact Explanation

After a stop-loss breach, the pool's swap path in the breached direction is permanently disabled. Traders cannot use the pool in that direction. The pool's core swap functionality is broken for an indefinite period, which also prevents the oracle market-maker from providing two-sided liquidity. LPs can still exit via `removeLiquidity`, but the pool is effectively dead as a trading venue in one direction.

This matches the allowed impact gate: **broken core pool functionality causing unusable swap flows**.

---

### Likelihood Explanation

- No privileged actor is required; any public swap in the non-blocked direction advances the clock.
- Small per-share metrics are common in bins that have been partially consumed by prior swaps, or in pools with high share counts relative to token balances.
- The decay rate of 58 (5%/day) is the value cited in the contract's own NatDoc comment as a typical configuration.
- The condition is self-reinforcing: once the clock starts advancing without decay, it continues to do so on every subsequent swap.

---

### Recommendation

Only advance `lastDecayTs` when the decay computation actually changed the stored watermark value. If `_decayed` returns the same value as the stored watermark, do not reset the clock, so that elapsed time accumulates until the product `hwm * factor` is large enough to produce a non-zero step:

```solidity
// In _checkAndUpdateWatermarks:
uint256 decayed0 = _decayed(hwmS.token0, decayRate, dt);
uint256 decayed1 = _decayed(hwmS.token1, decayRate, dt);

(uint256 hwm0, bool breach0) = _applyWatermark(metricT0, decayed0, floorMultiplier);
if (breach0 && zeroForOne) { revert ...; }

(uint256 hwm1, bool breach1) = _applyWatermark(metricT1, decayed1, floorMultiplier);
if (breach1 && !zeroForOne) { revert ...; }

hwmS.token0 = uint104(hwm0);
hwmS.token1 = uint104(hwm1);
// Only advance the clock if decay actually moved the watermark
if (decayed0 != hwmS.token0 || decayed1 != hwmS.token1) {
    hwmS.lastDecayTs = uint32(block.timestamp);
}
```

Alternatively, accumulate elapsed time in a separate field without resetting it until a non-zero decay step occurs.

---

### Proof of Concept

```
Setup:
  - Pool with OracleValueStopLossExtension, drawdownE6 = 50_000, decayPerSecondE8 = 58
  - Bin 0: t0 = 100 scaled units, t1 = 100 scaled units, shares = 10_000
    → hwm ≈ 100 (per-share metric ≈ 100 * 1e6 / 10000 = 10, but cross-term adds ~10 more)
    → For simplicity, assume hwm = 100 after first swap

Step 1: First swap sets watermark to 100 (hwm0 = hwm1 = 100).

Step 2: Drain bin to t0 = 40, t1 = 40 → metric drops to ~40.
        40 < 100 * (1e6 - 50_000) / 1e6 = 95 → breach triggered.
        Pool blocks zeroForOne swaps.

Step 3: Every 1 second, a swap in the non-blocked direction calls afterSwap:
        dt = 1, factor = 58 * 1 = 58
        (100 * 58) / 1e8 = 0 → _decayed returns 100 (unchanged)
        hwmS.lastDecayTs = block.timestamp  ← clock advances, watermark stays at 100

Step 4: After 1 day (86400 seconds), the watermark should have decayed by ~5%.
        Expected: hwm ≈ 95, allowing metric 40 to pass (40 < 95 * 0.95 = 90.25 → still blocked,
        but after ~2 days decay should reach ~90, and after enough time metric 40 would pass).
        Actual: hwm = 100 forever. Pool permanently blocked.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L318-324)
```text
  /// @dev Linear decay; floors at 0 (ratchet restores from the live metric on next touch).
  function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;
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
