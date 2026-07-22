### Title
Stale `lastDecayTs` After `executeOracleStopLossDecay` Retroactively Misapplies New Rate, Silently Disabling the Stop-Loss Guard — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`executeOracleStopLossDecay()` updates `oracleStopLossConfig[pool_].decayPerSecondE8` but never checkpoints the per-bin `highWatermarks[pool][binIdx].lastDecayTs`. Because decay is evaluated lazily on the next swap, the new rate is applied over the entire elapsed interval since the last swap — including the period before the rate change. A sufficiently large rate increase can zero out every bin's watermark retroactively, completely disabling the stop-loss guard for those bins.

---

### Finding Description

The `OracleValueStopLossExtension` tracks per-bin high watermarks and decays them lazily on each swap. The decay is computed in `_decayed()`: [1](#0-0) 

```solidity
function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;          // ← zeroed when factor ≥ 1e8
    return hwm - (hwm * factor) / E8;
}
```

`dt` is computed as `block.timestamp - hwmS.lastDecayTs`, where `lastDecayTs` is only updated after each swap in that bin: [2](#0-1) 

When the pool admin executes a decay rate change, only the config is updated — no bin watermarks are touched: [3](#0-2) 

```solidity
function executeOracleStopLossDecay(address pool_) external onlyPoolAdmin(pool_) {
    ...
    oracleStopLossConfig[pool_].decayPerSecondE8 = decay;   // ← only config updated
    // ← lastDecayTs for every bin is NOT reset / checkpointed
    ...
}
```

On the next swap, `_afterSwapOracleStopLoss` reads the **new** `decayPerSecondE8` from config and applies it over the **full** `dt` since the last swap — which includes the pre-change period: [4](#0-3) 

This is the exact same class of bug as the external report: a configuration setter changes a parameter that governs a guard calculation, but a dependent state variable (`lastDecayTs`, analogous to `unit`) is left stale, causing the guard to compute incorrect values.

---

### Impact Explanation

When `decayPerSecondE8` is increased, the new higher rate is applied retroactively over the entire idle period of each bin. For any bin where `newRate × dt ≥ 1e8`, `_decayed()` returns `0`, zeroing the watermark. With a zeroed watermark, `_applyWatermark(metric, 0, floor)` always returns `(metric, false)` — no breach is ever detected. The stop-loss guard is silently disabled for those bins, allowing value-draining swaps to proceed without triggering the revert, exposing LP principal to losses beyond the configured `drawdownE6` threshold. [5](#0-4) 

---

### Likelihood Explanation

The pool admin is a semi-trusted role that legitimately adjusts the decay rate over the pool's lifetime (e.g., tightening protection after a market event). The timelock mechanism delays execution but does not prevent the retroactive misapplication. Any bin that has not had a swap since `lastDecayTs` is vulnerable. In low-activity pools or during quiet periods, many bins can be idle for days, making the zeroing condition easy to satisfy with even a modest rate increase.

---

### Recommendation

Before writing the new `decayPerSecondE8`, checkpoint every bin's watermark by applying the **current** (old) rate up to `block.timestamp` and resetting `lastDecayTs`. Because the set of active bins is not enumerated on-chain, the simplest safe approach is:

1. Add a `checkpointBinWatermark(address pool_, int8 binIdx)` function that applies the current rate and resets `lastDecayTs` to `block.timestamp`.
2. Require (off-chain enforcement or on-chain guard) that all active bins are checkpointed before `executeOracleStopLossDecay` is called, or
3. Store the rate-change timestamp alongside the rate and apply piecewise decay in `_decayed` (rate₁ for `[lastDecayTs, changeTs]`, rate₂ for `[changeTs, now]`).

The minimal safe fix matching the external report's pattern: reset `lastDecayTs` for all bins to `block.timestamp` inside `executeOracleStopLossDecay` (treating the pre-change period as zero-decay, which is conservative but prevents guard bypass).

---

### Proof of Concept

```
Setup:
  decayPerSecondE8 = 58   (≈5%/day)
  drawdownE6       = 100_000 (10%)
  Bin X watermarks set to W = 1_000_000 at T0
  lastDecayTs[X]   = T0

Step 1 (T0 → T0+3days): No swaps in bin X.

Step 2 (T0+3days): Admin proposes decayPerSecondE8 = 580 (≈50%/day).
  Timelock elapses; admin calls executeOracleStopLossDecay().
  oracleStopLossConfig[pool].decayPerSecondE8 = 580
  lastDecayTs[X] is still T0.

Step 3 (T0+3days+1s): Attacker/anyone triggers a swap in bin X.
  dt     = 3 days + 1s = 259_201 seconds
  factor = 580 × 259_201 = 150_336_580  ≥ 1e8
  _decayed(1_000_000, 580, 259_201) → 0   ← watermark zeroed

Step 4: _applyWatermark(currentMetric, 0, floor):
  currentMetric ≥ 0 → no breach → stop-loss does NOT revert.

Result: Stop-loss guard is disabled for bin X.
  Value-draining swaps proceed unchecked, LP principal drains
  beyond the 10% drawdown threshold with no protection.
``` [3](#0-2) [1](#0-0) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L139-147)
```text
  function executeOracleStopLossDecay(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingDecayExecuteAfter == 0) revert OracleStopLossNoPendingDecay(pool_);
    _requireElapsed(sched.pendingDecayExecuteAfter);
    uint32 decay = sched.pendingDecayPerSecondE8;
    oracleStopLossConfig[pool_].decayPerSecondE8 = decay;
    (sched.pendingDecayPerSecondE8, sched.pendingDecayExecuteAfter) = (0, 0);
    emit OracleStopLossDecaySet(pool_, decay);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L235-242)
```text
    uint256 decayRate = cfg.decayPerSecondE8;
    for (uint256 i = 0; i < count; i++) {
      uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
      if (totalShares == 0) continue;
      (uint104 t0, uint104 t1,,,) = PoolStateLibrary._decodeBinState(states[i]);
      (uint256 metricT0, uint256 metricT1) = _metrics(t0, t1, totalShares, minShares, midPriceX64);
      _checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1, floorMultiplier, decayRate, zeroForOne);
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L319-324)
```text
  function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;
  }
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
