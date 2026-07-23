### Title
Stop-Loss Watermark Not Ratcheted on Cross-Direction Breach Revert, Allowing Subsequent Guard Bypass — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`_checkAndUpdateWatermarks` writes both watermarks and `lastDecayTs` only after both direction-specific breach checks pass. When a `zeroForOne` swap triggers `breach0 && zeroForOne` and reverts, the EVM rolls back all state, leaving `hwmS.token1` un-ratcheted and `hwmS.lastDecayTs` un-advanced. A subsequent `!zeroForOne` swap then compares `metricT1` against the stale, un-ratcheted (and further-decayed) token1 watermark instead of the higher value that should have been committed, allowing the swap to proceed when it should have been blocked.

---

### Finding Description

In `_checkAndUpdateWatermarks`:

```
(uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
if (breach0 && zeroForOne) {
    revert OracleStopLossTriggered(...);   // ← rolls back everything below
}

(uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
if (breach1 && !zeroForOne) { revert ...; }

hwmS.token0 = uint104(hwm0);   // ← never reached on breach0 revert
hwmS.token1 = uint104(hwm1);   // ← never reached on breach0 revert
hwmS.lastDecayTs = uint32(block.timestamp);  // ← never reached
``` [1](#0-0) 

`_applyWatermark` ratchets up when `metric >= hwm`:

```
if (metric >= hwm) return (metric, false);   // ratchet up, no breach
``` [2](#0-1) 

When oracle mid is high, `metricT0` is low (breach0 possible) and `metricT1` is high (would ratchet up to `C`). The revert prevents `hwmS.token1` from being updated to `C`. When mid later returns to normal, `metricT1` returns to `D ≈ B` (original level). The subsequent `!zeroForOne` swap compares `D` against the stale `B''` (original watermark, further decayed by the extra elapsed time since `lastDecayTs` was never advanced), so `D ≥ B''` → no breach → swap passes. Had the first swap succeeded, `hwmS.token1 = C` and the check would compare `D` against `C * floorMultiplier / E6 >> D` → breach → revert.

The metrics formula confirms the directional relationship:

```
metricT0 = t0*SCALE/shares + (t1 * 2^64 / mid) * SCALE / shares   // LOW when mid is HIGH
metricT1 = (t0 * mid / 2^64) * SCALE / shares + t1*SCALE/shares   // HIGH when mid is HIGH
``` [3](#0-2) 

---

### Impact Explanation

The token1 stop-loss guard is bypassed for `!zeroForOne` swaps following a reverted `zeroForOne` breach. The `!zeroForOne` direction drains token0 from the pool. The stop-loss is the LP's protection against value-per-share leakage; bypassing it allows the pool to be drained of token0 at a time when the oracle-derived value per share has dropped significantly from the ratcheted high, constituting a direct loss of LP principal above Sherlock Medium thresholds.

---

### Likelihood Explanation

The attack requires only natural market conditions (oracle mid spike followed by reversion) and two sequential public swaps. No privileged access, oracle manipulation, or non-standard token behavior is needed. The attacker's first (reverted) swap costs only gas. The window is open until any successful swap in either direction updates the watermarks. This is a realistic, repeatable sequence on any pool using this extension.

---

### Recommendation

Separate the watermark update from the breach check. Commit the ratcheted watermarks (and `lastDecayTs`) unconditionally before performing the direction-specific revert, or split the function into a pure compute step and a write step that always executes:

```solidity
// Compute new watermarks first
(uint256 hwm0, bool breach0) = _applyWatermark(...);
(uint256 hwm1, bool breach1) = _applyWatermark(...);

// Always persist — even if we are about to revert
hwmS.token0 = uint104(hwm0);
hwmS.token1 = uint104(hwm1);
hwmS.lastDecayTs = uint32(block.timestamp);

// Then enforce direction-specific stop-loss
if (breach0 && zeroForOne)  revert OracleStopLossTriggered(...);
if (breach1 && !zeroForOne) revert OracleStopLossTriggered(...);
```

This ensures the ratchet and decay clock are always advanced regardless of which direction triggers the stop-loss.

---

### Proof of Concept

1. Pool initialized with `drawdownE6 = 50_000` (5%), `decayPerSecondE8 > 0`. Initial watermarks: `hwmS.token0 = A`, `hwmS.token1 = B`, `hwmS.lastDecayTs = T0`.
2. Oracle mid doubles. `metricT0` drops below `A * 0.95` (breach0). `metricT1` rises to `C ≈ 2B`.
3. Attacker submits `zeroForOne = true` swap → `afterSwap` reverts with `OracleStopLossTriggered`. State unchanged. Cost: gas only.
4. Oracle mid returns to original. `metricT1` returns to `D ≈ B`.
5. Attacker submits `!zeroForOne` swap. `dt = T2 - T0` (large). `hwm1_decayed = _decayed(B, rate, T2-T0) ≈ B`. `D ≈ B ≥ B''` → no breach → swap executes, draining token0.
6. Assert: had step 3 succeeded, `hwmS.token1 = C`; step 5 would compare `D ≈ B` against `C * 0.95 ≈ 1.9B` → breach → revert. The bypass is confirmed. [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L253-256)
```text
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L333-335)
```text
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
    return (hwm, breached);
```
