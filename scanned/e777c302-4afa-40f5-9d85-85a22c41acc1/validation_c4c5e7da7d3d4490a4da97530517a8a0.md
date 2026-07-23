### Title
`executeOracleStopLossDecay` applies new decay rate retroactively to the full elapsed period since the last swap, silently zeroing watermarks and disabling stop-loss protection — (File: `metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`executeOracleStopLossDecay` stores a new `decayPerSecondE8` but never updates `lastDecayTs` in any bin's `BinHighWatermarks`. On the next swap, `_checkAndUpdateWatermarks` computes `dt = block.timestamp - hwmS.lastDecayTs` and feeds the new (higher) rate into `_decayed` over that entire elapsed window — retroactively applying the new rate to a period when the old, slower rate was active. If the pool admin sets the maximum allowed decay rate (`1e8`), a single second of elapsed time is enough to zero every watermark, completely disabling the stop-loss guard for the next trade.

---

### Finding Description

`executeOracleStopLossDecay` writes only to `oracleStopLossConfig[pool_].decayPerSecondE8`:

```solidity
// OracleValueStopLossExtension.sol L139-147
function executeOracleStopLossDecay(address pool_) external onlyPoolAdmin(pool_) {
    ...
    uint32 decay = sched.pendingDecayPerSecondE8;
    oracleStopLossConfig[pool_].decayPerSecondE8 = decay;   // ← only this changes
    ...
}
``` [1](#0-0) 

`lastDecayTs` lives inside each bin's `BinHighWatermarks` and is only written by `_checkAndUpdateWatermarks` (on every swap) and by `executeOracleStopLossHighWatermarks` (which explicitly resets it to `block.timestamp`):

```solidity
// L173-174 — watermark manual-set correctly resets the clock
highWatermarks[pool_][pending.binIdx] =
    BinHighWatermarks({token0: pending.token0, token1: pending.token1, lastDecayTs: uint32(block.timestamp)});
``` [2](#0-1) 

But `executeOracleStopLossDecay` performs no equivalent reset. On the next swap, `_checkAndUpdateWatermarks` computes:

```solidity
// L267-270
BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
uint256 dt = block.timestamp - hwmS.lastDecayTs;          // ← spans entire gap since last swap
(uint256 hwm0, bool breach0) =
    _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
``` [3](#0-2) 

`_decayed` uses the new rate over the full `dt`:

```solidity
// L319-324
function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;          // ← zeroed when rate × elapsed ≥ 1e8
    return hwm - (hwm * factor) / E8;
}
``` [4](#0-3) 

`_validateDecay` permits rates up to `1e8` (100 %/second):

```solidity
// L309-311
function _validateDecay(uint256 decayPerSecondE8) private pure {
    if (decayPerSecondE8 > E8) revert OracleStopLossDecayTooLarge(decayPerSecondE8);
}
``` [5](#0-4) 

So with `decayPerSecondE8 = 1e8` and `dt ≥ 1 second`, `factor = 1e8 * 1 = 1e8 ≥ E8`, and every watermark is zeroed. `_applyWatermark` then sees `hwm = 0`, so `metric >= hwm` is always true, `breached = false`, and the stop-loss never fires regardless of how far the pool value has fallen. [6](#0-5) 

The M-19 structural parallel is exact: just as `feesUpdatedAt` was only updated when `managementFee > 0` (allowing a new fee rate to be applied retroactively to a period when a lower rate was active), `lastDecayTs` is never updated when `decayPerSecondE8` changes, allowing the new rate to be applied retroactively to the entire period since the last swap.

---

### Impact Explanation

The stop-loss extension's stated guarantee is: *"value per share at oracle marks cannot fall faster than drawdown (one-time) + decay × t (ongoing)."* [7](#0-6) 

Retroactive zeroing of watermarks breaks this guarantee entirely. A swap that should have been blocked — because the pool's per-share value has fallen below the drawdown floor relative to the established watermark — is allowed to proceed. LP principal drains through the unguarded swap. This is a direct loss of LP funds, not a theoretical edge case: any pool whose value has already declined to within the drawdown band is immediately exposed the moment the watermarks are zeroed.

---

### Likelihood Explanation

The pool admin is semi-trusted and must pass the timelock before executing the change. However:

1. The timelock itself does not prevent the retroactive effect — it only delays when the new rate is stored. Once stored, the retroactive application happens automatically on the next public swap.
2. The timelock period can itself be reduced via `executeOracleStopLossTimelock` (also timelocked, but the initial timelock can be set to zero at pool creation).
3. The maximum allowed decay rate (`1e8`) is reachable through the normal admin flow; no cap prevents it.
4. Any public trader can trigger the first post-change swap, causing the watermarks to be zeroed and the stop-loss to fail open — the pool admin does not need to be the one executing the swap. [8](#0-7) 

---

### Recommendation

In `executeOracleStopLossDecay`, before writing the new rate, apply the old decay rate to every active bin's watermark and reset `lastDecayTs` to `block.timestamp`. This mirrors the pattern already used in `executeOracleStopLossHighWatermarks` (which correctly resets `lastDecayTs` when watermarks are manually set) and the pattern used in `MetricOmmPoolFactory.setPoolAdminFees` (which collects accrued fees at the old rate before storing the new rate). [9](#0-8) [10](#0-9) 

Concretely: iterate over all bins in the pool's configured range, read each `BinHighWatermarks`, apply `_decayed(hwm, oldRate, block.timestamp - lastDecayTs)`, write the decayed value back, and set `lastDecayTs = block.timestamp`. Then store the new `decayPerSecondE8`. This ensures the new rate is only applied to time elapsed after the change, not retroactively.

---

### Proof of Concept

```
State at T=0:
  decayPerSecondE8 = 10 (slow)
  Bin 0 watermark token0 = 1000, lastDecayTs = T=0

T=1: Swap occurs → watermark stays ~1000, lastDecayTs = T=1

T=2: Pool admin proposes decayPerSecondE8 = 1e8 (max)
     Timelock = 3 days → executeAfter = T=2 + 3 days

T=2 + 3 days: Admin calls executeOracleStopLossDecay
  → oracleStopLossConfig[pool].decayPerSecondE8 = 1e8
  → lastDecayTs in Bin 0 is still T=1 (NOT updated)

T=2 + 3 days + 2 seconds: Public trader calls swap
  → _checkAndUpdateWatermarks called
  → dt = (T=2 + 3 days + 2s) - T=1 ≈ 3 days + 1s >> 1 second
  → factor = 1e8 * dt >> 1e8 → _decayed returns 0
  → hwm0 = 0, hwm1 = 0
  → _applyWatermark: metric >= 0 always → breached = false
  → Stop-loss does NOT trigger
  → Value-leaking swap executes, LP funds drain
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L27-28)
```text
///      Watermarks decay linearly at decayPerSecondE8 (lazy, per bin). Guarantee: value per
///      share at oracle marks cannot fall faster than drawdown (one-time) + decay * t (ongoing).
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L86-94)
```text
  function executeOracleStopLossTimelock(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingTimelockExecuteAfter == 0) revert OracleStopLossNoPendingTimelock(pool_);
    _requireElapsed(sched.pendingTimelockExecuteAfter);
    uint32 timelock = sched.pendingTimelock;
    oracleStopLossConfig[pool_].timelock = timelock;
    (sched.pendingTimelock, sched.pendingTimelockExecuteAfter) = (0, 0);
    emit OracleStopLossTimelockSet(pool_, timelock);
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L168-177)
```text
  /// @notice Apply the pending watermarks. Also resets the decay clock for the bin.
  function executeOracleStopLossHighWatermarks(address pool_) external onlyPoolAdmin(pool_) {
    PendingHighWatermarks memory pending = pendingHighWatermark[pool_];
    if (pending.executeAfter == 0) revert OracleStopLossNoPendingHighWatermark(pool_);
    _requireElapsed(pending.executeAfter);
    highWatermarks[pool_][pending.binIdx] =
      BinHighWatermarks({token0: pending.token0, token1: pending.token1, lastDecayTs: uint32(block.timestamp)});
    delete pendingHighWatermark[pool_];
    emit OracleStopLossHighWatermarkUpdated(pool_, pending.binIdx, pending.token0, pending.token1);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L267-270)
```text
    BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
    uint256 dt = block.timestamp - hwmS.lastDecayTs;

    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L309-311)
```text
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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L417-425)
```text
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );
```
