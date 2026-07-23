### Title
Stop-Loss Watermarks Not Settled at Old Decay Rate Before `executeOracleStopLossDecay` Applies New Rate — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension` uses a lazy-decay model: watermarks are only decayed when `_checkAndUpdateWatermarks` is called inside `afterSwap`. When the pool admin executes a decay-rate change via `executeOracleStopLossDecay`, the new rate is stored immediately but the stored `lastDecayTs` in every bin's `BinHighWatermarks` is **not updated**. The next `afterSwap` call then applies the new rate retroactively over the entire elapsed period `dt = block.timestamp − lastDecayTs`, which includes the timelock waiting period. A pool admin can choose a new rate that, when multiplied by this accumulated `dt`, meets or exceeds `E8` in `_decayed`, collapsing every touched watermark to zero and permanently disabling the stop-loss guard until watermarks are rebuilt organically.

---

### Finding Description

`executeOracleStopLossDecay` writes the new rate directly into `oracleStopLossConfig` without first flushing the per-bin watermarks at the old rate:

```solidity
// OracleValueStopLossExtension.sol lines 139-147
function executeOracleStopLossDecay(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingDecayExecuteAfter == 0) revert OracleStopLossNoPendingDecay(pool_);
    _requireElapsed(sched.pendingDecayExecuteAfter);
    uint32 decay = sched.pendingDecayPerSecondE8;
    oracleStopLossConfig[pool_].decayPerSecondE8 = decay;   // ← new rate stored
    (sched.pendingDecayPerSecondE8, sched.pendingDecayExecuteAfter) = (0, 0);
    emit OracleStopLossDecaySet(pool_, decay);
    // ← lastDecayTs in highWatermarks[pool][*] is never touched
}
``` [1](#0-0) 

The lazy decay helper that is called on the next swap:

```solidity
// lines 319-324
function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;          // ← collapses to 0
    return hwm - (hwm * factor) / E8;
}
``` [2](#0-1) 

`dt` at the moment of the first post-execution swap equals `block.timestamp − hwmS.lastDecayTs`, which includes the entire timelock waiting period plus any idle time before the proposal. The new rate is applied to this full accumulated `dt`, not just to the time after the rate change took effect.

The collapse condition `factor = newRate × dt ≥ 1e8` is easy to reach. For example:

| Timelock | New rate (E8) | `dt` needed to collapse |
|---|---|---|
| 1 day (86 400 s) | 1 157 | 86 400 s (exactly the timelock) |
| 7 days (604 800 s) | 166 | 604 800 s |
| 0 s | 1e8 (max) | 1 s | [3](#0-2) 

Once `hwm0` and `hwm1` are zero, `_applyWatermark` always returns `(metric, false)` — the breach flag is never set — so the stop-loss can never trigger for those bins until new high-watermarks are organically ratcheted up through subsequent swaps. [4](#0-3) 

---

### Impact Explanation

The `OracleValueStopLossExtension` is the primary on-chain guard that prevents LP value-per-share from falling below a configured floor. Collapsing all watermarks to zero silently disables this guard for every bin touched by the next swap. During the resulting unguarded window, any swap that would otherwise have been blocked by the stop-loss (e.g., one driven by a stale or manipulated oracle price) executes freely, directly draining LP token balances. This is a broken core pool safety mechanism with a direct path to loss of LP principal.

---

### Likelihood Explanation

The trigger is the pool admin executing a timelocked decay-rate change — a routine administrative action explicitly supported by the protocol. The pool admin need not act maliciously; even a well-intentioned admin increasing the decay rate to a value that is individually reasonable (e.g., 1 157 per second ≈ 1 %/day) will collapse watermarks if the pool has been idle for the duration of the timelock. The timelock is supposed to give LPs time to react to parameter changes, but the retroactive application means the watermarks collapse instantaneously at execution time, not gradually, removing the protection the timelock was designed to provide.

---

### Recommendation

Before writing the new decay rate, settle all active watermarks at the old rate by updating `lastDecayTs` to `block.timestamp` for every bin that has a non-zero watermark. Because iterating all bins on-chain is impractical, the simplest safe fix is to record the rate-change timestamp and apply a two-segment decay in `_decayed`: old rate × time-before-change + new rate × time-after-change. Alternatively, require that `executeOracleStopLossDecay` also resets `lastDecayTs` on all bins (enforced off-chain via a helper) or that the new rate may only be applied from the current block forward by snapshotting `lastDecayTs = block.timestamp` for every bin at execution time.

---

### Proof of Concept

1. Pool is deployed with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5 %), `decayPerSecondE8 = 0`, `timelock = 86_400` (1 day).
2. Several swaps occur; bin 0 watermarks are set to non-zero values (e.g., `hwm0 = hwm1 = 1e6`). `lastDecayTs` is set to `T0`.
3. Pool goes idle. No swaps for 1 day.
4. Pool admin calls `proposeOracleStopLossDecay(pool, 1_157)` at `T0 + 86_400`.
5. After the 1-day timelock, admin calls `executeOracleStopLossDecay(pool)` at `T0 + 172_800`.
6. Next swap triggers `afterSwap`. `_checkAndUpdateWatermarks` computes `dt = T0 + 172_800 − T0 = 172_800`. `factor = 1_157 × 172_800 = 199_929_600 > 1e8`. `_decayed` returns 0 for both `hwm0` and `hwm1`.
7. `_applyWatermark(metric, 0, floorMultiplier)` → `metric >= 0` is always true → `breached = false`. Stop-loss never fires.
8. Subsequent swaps that drain LP value below the original 5 % drawdown floor proceed without revert. [1](#0-0) [2](#0-1) [5](#0-4)

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
