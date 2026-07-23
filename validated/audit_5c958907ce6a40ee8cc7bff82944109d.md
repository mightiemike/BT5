### Title
Stale `lastDecayTs` after `executeOracleStopLossDecay()` allows retroactive watermark wipeout, bypassing stop-loss protection — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`executeOracleStopLossDecay()` updates `decayPerSecondE8` but never resets `lastDecayTs` in the per-bin `highWatermarks`. The next `afterSwap` call applies the new rate retroactively to the entire elapsed time since the last watermark touch. When the elapsed time is large enough, the watermarks decay to zero, the stop-loss floor collapses to zero, and every subsequent swap is permitted regardless of value loss — directly contradicting the protection the timelock was designed to guarantee.

---

### Finding Description

`executeOracleStopLossDecay()` writes the new rate and clears the schedule, but performs no watermark housekeeping:

```solidity
function executeOracleStopLossDecay(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingDecayExecuteAfter == 0) revert OracleStopLossNoPendingDecay(pool_);
    _requireElapsed(sched.pendingDecayExecuteAfter);
    uint32 decay = sched.pendingDecayPerSecondE8;
    oracleStopLossConfig[pool_].decayPerSecondE8 = decay;   // ← only write
    (sched.pendingDecayPerSecondE8, sched.pendingDecayExecuteAfter) = (0, 0);
    emit OracleStopLossDecaySet(pool_, decay);
}
``` [1](#0-0) 

`lastDecayTs` lives inside `BinHighWatermarks` and is only updated inside `_checkAndUpdateWatermarks()`, which is called from `afterSwap`. Between two swaps it is never touched. [2](#0-1) 

The lazy decay formula is:

```solidity
function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;          // ← watermark wiped to zero
    return hwm - (hwm * factor) / E8;
}
``` [3](#0-2) 

`dt` is computed as `block.timestamp - hwmS.lastDecayTs`. Because `lastDecayTs` was never reset when the rate changed, `dt` spans the entire period from the last swap to the current block — not just the period since the rate change.

**Concrete wipeout scenario:**

| Variable | Value |
|---|---|
| Old `decayPerSecondE8` | 58 (≈5 %/day) |
| New `decayPerSecondE8` | 1 200 (≈103 %/day) |
| Time since last swap | 24 h = 86 400 s |
| `factor` | 1 200 × 86 400 = 103 680 000 |
| `E8` | 100 000 000 |
| Result | `factor >= E8` → watermark = **0** |

With watermarks at zero, `_applyWatermark` always returns `(metric, false)` — `breached` is never `true` — so `OracleStopLossTriggered` is never emitted and every swap direction is permitted. [4](#0-3) 

The contrast with `executeOracleStopLossHighWatermarks()` is instructive: that function explicitly resets `lastDecayTs = block.timestamp` when it writes new watermarks, precisely to avoid stale-clock problems:

```solidity
highWatermarks[pool_][pending.binIdx] =
    BinHighWatermarks({token0: pending.token0, token1: pending.token1, lastDecayTs: uint32(block.timestamp)});
``` [5](#0-4) 

`executeOracleStopLossDecay()` performs no equivalent reset.

---

### Impact Explanation

When the pool admin legitimately increases the decay rate (a permissive, LP-facing change), the retroactive application of the new rate to the full elapsed `dt` can instantly zero every active watermark. The stop-loss floor collapses to zero for all bins touched by the next swap. Value-leaking swaps that the extension was configured to block are permitted, causing direct loss of LP principal. The timelock that was supposed to give LPs time to react is rendered ineffective: the damage occurs on the first swap after `executeOracleStopLossDecay()` is called, not gradually over the new rate's intended horizon.

---

### Likelihood Explanation

The pool admin is a semi-trusted role with timelocked controls specifically because LPs must be able to react to parameter changes. Increasing the decay rate is a routine operational action (e.g., adjusting to new market volatility). The condition `newRate × dt ≥ E8` is reachable whenever the pool has been quiet for more than `E8 / newRate` seconds — for a rate of 1 200 that is only ~23 hours. Pools with infrequent swap activity (common for illiquid or newly launched pools) are permanently exposed.

---

### Recommendation

Before writing the new rate, apply the old rate to all active watermarks up to `block.timestamp`, then reset `lastDecayTs`. The pattern already used by `executeOracleStopLossHighWatermarks()` — setting `lastDecayTs = block.timestamp` — should be replicated here. Because bins are iterable only off-chain, the simplest on-chain fix is to store a `rateChangedAt` timestamp alongside the rate and perform a two-phase decay in `_decayed()`:

```diff
function executeOracleStopLossDecay(address pool_) external onlyPoolAdmin(pool_) {
    ...
    uint32 decay = sched.pendingDecayPerSecondE8;
+   // Snapshot the rate-change timestamp so _decayed() can split old/new periods.
+   oracleStopLossConfig[pool_].decayChangedAt = uint32(block.timestamp);
    oracleStopLossConfig[pool_].decayPerSecondE8 = decay;
    ...
}
```

Alternatively, require the admin to call `setLastMidPrice`-style watermark resets for every active bin before executing a decay increase, or cap the retroactive `dt` to the timelock duration.

---

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension`, `drawdownE6 = 100_000` (10 %), `decayPerSecondE8 = 58`, `timelock = 0`.
2. Execute one swap to set watermarks (e.g., `hwm0 = hwm1 = 1e6`). `lastDecayTs` is now `T0`.
3. Warp forward 24 hours (`T0 + 86400`). No swaps occur.
4. Admin calls `proposeOracleStopLossDecay(pool, 1200)` then immediately `executeOracleStopLossDecay(pool)`.
5. Execute a value-leaking swap (metric drops 50 % below the old floor).
6. Observe: `_decayed(1e6, 1200, 86400)` → `factor = 103_680_000 ≥ E8` → returns `0`. `_applyWatermark(metric, 0, 900_000)` → `metric >= 0` → `breached = false`. The swap succeeds; `OracleStopLossTriggered` is never emitted.
7. Without step 4, the same swap at step 5 would revert because the watermark would still be `~499_000` and the floor `~449_000`, which the degraded metric breaches.

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L173-176)
```text
    highWatermarks[pool_][pending.binIdx] =
      BinHighWatermarks({token0: pending.token0, token1: pending.token1, lastDecayTs: uint32(block.timestamp)});
    delete pendingHighWatermark[pool_];
    emit OracleStopLossHighWatermarkUpdated(pool_, pending.binIdx, pending.token0, pending.token1);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L267-285)
```text
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
