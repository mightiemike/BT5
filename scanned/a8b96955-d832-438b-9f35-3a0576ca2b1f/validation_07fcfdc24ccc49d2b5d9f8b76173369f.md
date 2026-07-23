### Title
`OracleValueStopLossExtension` Retroactively Applies New `decayPerSecondE8` to the Full Elapsed Period, Silently Collapsing Watermarks and Bypassing Stop-Loss Protection — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

When the pool admin executes a `decayPerSecondE8` increase via `executeOracleStopLossDecay`, the new rate is immediately applied to the **entire** elapsed period since `lastDecayTs` in both `currentHighWatermarks()` and the enforcement path `_checkAndUpdateWatermarks()`. Because the watermark was stored under the old rate, the retroactive application over-decays it — potentially to zero — silently disabling the stop-loss guard on the very next swap, even though the timelock was supposed to give LPs time to react.

---

### Finding Description

The `OracleValueStopLossExtension` tracks per-bin value-per-share watermarks and decays them linearly at `decayPerSecondE8` to allow natural impermanent-loss drift. The decay is computed lazily on each swap in `_checkAndUpdateWatermarks`:

```solidity
// OracleValueStopLossExtension.sol  _afterSwapOracleStopLoss
PoolStopLossConfig memory cfg = oracleStopLossConfig[pool_];
uint256 decayRate = cfg.decayPerSecondE8;          // ← current rate, post-change
...
uint256 dt = block.timestamp - hwmS.lastDecayTs;   // ← full elapsed period
(uint256 hwm0, bool breach0) = _applyWatermark(
    metricT0,
    _decayed(hwmS.token0, decayRate, dt),           // ← new rate × full dt
    floorMultiplier
);
``` [1](#0-0) 

The `_decayed` helper applies a single linear factor over the whole `dt`:

```solidity
function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;
}
``` [2](#0-1) 

The same pattern appears in the public view `currentHighWatermarks()`:

```solidity
function currentHighWatermarks(address pool, int8 binIdx) external view returns (uint256 hwm0, uint256 hwm1) {
    BinHighWatermarks memory hwm = highWatermarks[pool][binIdx];
    uint256 rate = oracleStopLossConfig[pool].decayPerSecondE8;   // ← current rate
    uint256 dt = block.timestamp - hwm.lastDecayTs;
    return (_decayed(hwm.token0, rate, dt), _decayed(hwm.token1, rate, dt));
}
``` [3](#0-2) 

The rate is changed by the pool admin through a propose/execute flow:

```solidity
function executeOracleStopLossDecay(address pool_) external onlyPoolAdmin(pool_) {
    ...
    oracleStopLossConfig[pool_].decayPerSecondE8 = decay;   // ← stored, no checkpoint
    ...
}
``` [4](#0-3) 

**No checkpoint is written to `lastDecayTs` or to the stored watermark values at the moment the rate changes.** The next swap therefore applies the new rate over the entire `[lastDecayTs, now]` window, which may span days or weeks if the pool was idle.

Correct behaviour would be to checkpoint the watermark at the old rate up to the moment of the rate change, then begin decaying at the new rate from that point forward.

---

### Impact Explanation

**Direct loss of LP principal.** The `OracleValueStopLossExtension` is the mechanism that blocks swaps whose output would push per-share bin value below `hwm × (1 − drawdown)`. If the watermark is over-decayed to zero, `_applyWatermark` always returns `(metric, false)` — no breach is ever detected — and the guard is completely silent regardless of how much value is extracted from the bin. [5](#0-4) 

An attacker who controls the pool admin key (or who can front-run the rate execution) can:
1. Wait for a quiet period (no swaps, so `lastDecayTs` is stale).
2. Execute the pending high-rate decay change.
3. Immediately execute a large directional swap that drains the bin below the drawdown floor.
4. The guard does not revert because the watermark was collapsed to zero retroactively.

Even without adversarial intent, a well-meaning admin who raises the decay rate after a quiet weekend will silently disable the guard for the first swap after the change.

---

### Likelihood Explanation

- The pool admin is **semi-trusted** and the rate-change path is a normal operational action (the timelock exists precisely because this is expected to happen).
- The timelock delays the change but does **not** prevent the retroactive collapse — the collapse happens at the moment of the first swap after `executeOracleStopLossDecay`, not at the moment of the proposal.
- Any pool that experiences a quiet period (no swaps for hours or days) followed by a decay-rate increase is vulnerable. This is a realistic operational sequence.
- The maximum allowed rate is `1e8` per second; at that rate, `factor = 1e8 × dt ≥ 1e8` after just **1 second** of elapsed time, instantly zeroing any watermark. [6](#0-5) 

---

### Recommendation

Checkpoint the watermark at the current (old) rate before storing the new rate. In `executeOracleStopLossDecay`, iterate over all bins that have non-zero watermarks and call a `_decayBin(pool_, binIdx, oldRate)` helper that writes the decayed value and resets `lastDecayTs = block.timestamp`. After that, store the new rate. This ensures the new rate is only applied to time elapsed **after** the change.

Alternatively, store a `rateChangedAt` timestamp alongside the new rate and split the `dt` in `_decayed` into two segments: `[lastDecayTs, rateChangedAt]` at the old rate and `[rateChangedAt, now]` at the new rate.

---

### Proof of Concept

**Setup:**
- Pool configured with `OracleValueStopLossExtension`: `drawdownE6 = 100_000` (10%), `decayPerSecondE8 = 58` (~5 %/day), `timelock = 1 day`.
- Watermarks set at `hwm0 = hwm1 = 1_000` (arbitrary units) at `T0`; `lastDecayTs = T0`.

**Attack sequence:**

| Step | Time | Action |
|------|------|--------|
| 1 | T0 | Watermarks set; `lastDecayTs = T0`. |
| 2 | T0 | Admin proposes `decayPerSecondE8 = 580` (10× increase). |
| 3 | T0 + 1 day | Timelock elapses; admin calls `executeOracleStopLossDecay`. New rate stored; **no checkpoint written**. |
| 4 | T0 + 7 days | First swap arrives. `dt = 7 days = 604 800 s`. |

**Incorrect computation (current code):**
```
factor = 580 × 604_800 = 350_784_000 ≥ 1e8  →  _decayed returns 0
```
Watermark collapses to **0**. Guard never fires. Swap drains the bin.

**Correct computation (with checkpoint):**
```
Phase 1 (6 days at rate 58):  factor = 58 × 518_400 = 30_067_200 < 1e8
  hwm_after_phase1 = 1000 × (1 − 0.3007) = 699

Phase 2 (1 day at rate 580):  factor = 580 × 86_400 = 50_112_000 < 1e8
  hwm_final = 699 × (1 − 0.5011) = 349
```
Watermark remains at **349**. Floor = `349 × 0.9 = 314`. Guard correctly blocks any swap that pushes per-share value below 314.

The difference — **0 vs 349** — is the corrupted value. Any swap that extracts value between those two thresholds is incorrectly permitted, constituting a direct loss of LP principal that the extension was deployed to prevent.

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L71-76)
```text
  function currentHighWatermarks(address pool, int8 binIdx) external view returns (uint256 hwm0, uint256 hwm1) {
    BinHighWatermarks memory hwm = highWatermarks[pool][binIdx];
    uint256 rate = oracleStopLossConfig[pool].decayPerSecondE8;
    uint256 dt = block.timestamp - hwm.lastDecayTs;
    return (_decayed(hwm.token0, rate, dt), _decayed(hwm.token1, rate, dt));
  }
```

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L215-241)
```text
    PoolStopLossConfig memory cfg = oracleStopLossConfig[pool_];
    uint256 drawdown = cfg.drawdownE6;
    if (drawdown == 0) return;
    uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
    uint256 minShares = IMetricOmmPool(pool_).getImmutables().minimalMintableLiquidity;
    if (minShares == 0) minShares = 1;
    PoolSlot0 memory s0 = Slot0Library.unpack(packedSlot0Initial);
    PoolSlot0 memory s1 = Slot0Library.unpack(packedSlot0Final);
    int8 lo = s0.curBinIdx < s1.curBinIdx ? s0.curBinIdx : s1.curBinIdx;
    int8 hi = s0.curBinIdx > s1.curBinIdx ? s0.curBinIdx : s1.curBinIdx;
    // forge-lint: disable-next-line(unsafe-typecast)
    uint256 count = uint256(int256(hi) - int256(lo) + 1);
    int8[] memory binIdxs = new int8[](count);
    for (uint256 i = 0; i < count; i++) {
      // forge-lint: disable-next-line(unsafe-typecast)
      binIdxs[i] = int8(int256(lo) + int256(i));
    }
    bytes32[] memory states = PoolStateLibrary._multipleBinStates(pool_, binIdxs);
    bytes32[] memory shares = PoolStateLibrary._multipleBinTotalShares(pool_, binIdxs);
    uint256 floorMultiplier = E6 - drawdown;
    uint256 decayRate = cfg.decayPerSecondE8;
    for (uint256 i = 0; i < count; i++) {
      uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
      if (totalShares == 0) continue;
      (uint104 t0, uint104 t1,,,) = PoolStateLibrary._decodeBinState(states[i]);
      (uint256 metricT0, uint256 metricT1) = _metrics(t0, t1, totalShares, minShares, midPriceX64);
      _checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1, floorMultiplier, decayRate, zeroForOne);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L305-311)
```text
  function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
  }

  function _validateDecay(uint256 decayPerSecondE8) private pure {
    if (decayPerSecondE8 > E8) revert OracleStopLossDecayTooLarge(decayPerSecondE8);
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
