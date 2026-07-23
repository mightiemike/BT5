### Title
Retrospective Decay-Rate Application in `OracleValueStopLossExtension` Retroactively Zeros Stop-Loss Watermarks, Exposing LP Principal — (File: `metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension` computes watermark decay lazily: the full elapsed time `dt = block.timestamp − lastDecayTs` is multiplied by the **current** `decayPerSecondE8` on every swap. When the pool admin executes a timelocked decay-rate increase via `executeOracleStopLossDecay`, the new rate is applied retroactively over the entire period since the last swap — not just the period after the change. For a pool that has been idle for longer than `1e8 / newRate` seconds, the very next swap after the rate change decays every touched bin's watermark to zero, permanently disabling the stop-loss guard for those bins. LPs who stayed in the pool relying on the timelock's protection suffer the full loss that the stop-loss was designed to prevent.

---

### Finding Description

`executeOracleStopLossDecay` writes the new rate into `oracleStopLossConfig[pool_].decayPerSecondE8` but does **not** touch `BinHighWatermarks.lastDecayTs` for any bin:

```solidity
// OracleValueStopLossExtension.sol  lines 139-147
function executeOracleStopLossDecay(address pool_) external onlyPoolAdmin(pool_) {
    ...
    uint32 decay = sched.pendingDecayPerSecondE8;
    oracleStopLossConfig[pool_].decayPerSecondE8 = decay;   // ← rate updated
    // lastDecayTs for every bin is NOT reset here
    ...
}
```

The next swap calls `_checkAndUpdateWatermarks`, which reads the stored `lastDecayTs` and computes:

```solidity
// lines 268-284
uint256 dt = block.timestamp - hwmS.lastDecayTs;
(uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
```

`_decayed` applies the **new** rate over the **entire** elapsed time:

```solidity
// lines 319-324
function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;          // ← watermark zeroed when factor ≥ 1e8
    return hwm - (hwm * factor) / E8;
}
```

**Concrete scenario:**

| Step | Time | Event |
|---|---|---|
| T₀ | day 0 | Last swap; `lastDecayTs` set; `decayPerSecondE8 = 0` |
| T₁ | day 30 | Admin proposes `decayPerSecondE8 = 1 000` (0.001 %/s) |
| T₂ | day 37 | Timelock elapses; admin executes — rate stored, `lastDecayTs` unchanged |
| T₃ | day 37+ε | First swap: `dt = 37 days = 3 196 800 s`; `factor = 1 000 × 3 196 800 = 3.2×10⁹ ≥ 1e8` → `_decayed` returns **0** |

All bin watermarks are zeroed. `_applyWatermark` never reports a breach (`metric >= 0` always), so the stop-loss never fires again for those bins regardless of how far the per-share value falls.

The maximum allowed rate is `1e8` (100 %/s), so even a modest rate of `1 000` retroactively wipes watermarks after ~28 hours of pool inactivity. The timelock (designed to give LPs time to exit) does not prevent the retroactive erasure because `lastDecayTs` is not snapshotted at execution time.

---

### Impact Explanation

The stop-loss extension's sole purpose is to cap LP value loss per bin. Once watermarks are zeroed retroactively, the guard is permanently disabled for those bins: any subsequent swap — including one that extracts all remaining token0 or token1 from a bin — passes the `afterSwap` check without triggering `OracleStopLossTriggered`. LPs suffer direct loss of principal that the extension was contractually supposed to prevent. The loss is bounded only by the bin's total balance, which can be the entire LP deposit.

---

### Likelihood Explanation

The trigger is the pool admin executing a timelocked decay-rate increase — a routine administrative action explicitly supported by the contract. No malicious setup is required: the admin simply proposes a higher rate, waits for the timelock, and executes. The retrospective zeroing is most severe when the pool has been swap-idle for a period exceeding `1e8 / newRate` seconds before the execution, a condition that naturally arises in low-volume pools or during market downtime. The pool admin is semi-trusted; the timelock is the only protection mechanism, and it is bypassed by this path.

---

### Recommendation

When `executeOracleStopLossDecay` is called, snapshot the accumulated decay at the **old** rate for every bin before writing the new rate. The simplest fix is to reset `lastDecayTs` to `block.timestamp` for all bins at execution time (applying the old-rate decay first), or to store a "rate-change checkpoint" per bin so that `_decayed` can split the elapsed interval at the rate-change boundary:

```solidity
// Pseudocode fix in executeOracleStopLossDecay:
uint32 oldRate = oracleStopLossConfig[pool_].decayPerSecondE8;
// For each bin, apply old-rate decay and reset lastDecayTs:
for each binIdx in pool bins:
    BinHighWatermarks storage hwm = highWatermarks[pool_][binIdx];
    uint256 dt = block.timestamp - hwm.lastDecayTs;
    hwm.token0 = uint104(_decayed(hwm.token0, oldRate, dt));
    hwm.token1 = uint104(_decayed(hwm.token1, oldRate, dt));
    hwm.lastDecayTs = uint32(block.timestamp);
// Then apply the new rate:
oracleStopLossConfig[pool_].decayPerSecondE8 = decay;
```

Alternatively, store the rate alongside `lastDecayTs` in `BinHighWatermarks` so `_decayed` can reconstruct the piecewise decay history.

---

### Proof of Concept

```solidity
// Scenario: pool idle 30 days, admin raises decay rate, next swap zeroes watermarks

// 1. Pool deployed with decayPerSecondE8 = 0, drawdownE6 = 50_000 (5%)
// 2. Swaps occur; watermarks set to, say, hwm0 = hwm1 = 1e6 (METRIC_SCALE)
//    lastDecayTs = block.timestamp (day 0)

// 3. Pool goes idle for 30 days (no swaps → lastDecayTs stays at day 0)
vm.warp(block.timestamp + 30 days);

// 4. Admin proposes decayPerSecondE8 = 1_000 (0.001%/s ≈ 86%/day)
extension.proposeOracleStopLossDecay(pool, 1_000);

// 5. Timelock elapses (e.g. 7 days)
vm.warp(block.timestamp + 7 days);  // now 37 days since last swap

// 6. Admin executes — lastDecayTs for all bins is still day 0
extension.executeOracleStopLossDecay(pool);

// 7. Next swap: dt = 37 days = 3_196_800 s
//    factor = 1_000 * 3_196_800 = 3_196_800_000 >= 1e8  → _decayed returns 0
//    hwm0 = hwm1 = 0  → _applyWatermark never breaches  → stop-loss disabled

// 8. Adversarial swap drains the bin; OracleStopLossTriggered is never emitted
// LP loses full bin balance with no stop-loss protection
```

The root cause is in `executeOracleStopLossDecay` (lines 139–147) and `_decayed` (lines 319–324) of `metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`. [1](#0-0) [2](#0-1) [3](#0-2)

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
