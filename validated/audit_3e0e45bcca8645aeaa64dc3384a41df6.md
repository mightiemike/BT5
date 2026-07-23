The code confirms the claim. Let me verify the key structural facts:

1. `executeOracleStopLossDecay` (L139-147) writes `oracleStopLossConfig[pool_].decayPerSecondE8 = decay` with no watermark settlement and no `lastDecayTs` advancement. [1](#0-0) 

2. At swap time, `decayRate` is read from the live config (L235), `dt` spans the full gap since the last swap (L268), and `_decayed` applies the new rate to the entire `dt` (L270). [2](#0-1) [3](#0-2) 

3. `_decayed` uses a single rate for the full interval with no piecewise split. [4](#0-3) 

The PoC math is correct: with `decayPerSecondE8 = 1_000` and `dt = 86_401`, `factor = 86_401_000 ≥ 1e8`, so `_decayed` returns `0`, the floor collapses to `0`, and the stop-loss guard is completely disabled for the bin. The timelock only delays when the new rate takes effect, not whether the retroactive application occurs. No existing guard prevents this.

---

Audit Report

## Title
`executeOracleStopLossDecay` Retroactively Applies New Decay Rate to Elapsed Period Without Settling Per-Bin Watermarks — (`File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary
`executeOracleStopLossDecay` writes a new `decayPerSecondE8` directly into `oracleStopLossConfig` without first applying the old rate to stored per-bin `highWatermarks` or advancing `lastDecayTs`. Because decay is computed lazily at swap time using the full `dt = block.timestamp - hwmS.lastDecayTs`, the new rate is applied retroactively to the entire elapsed period since the last swap. When the new rate is high enough that `rateNew × dt ≥ 1e8`, `_decayed` returns `0`, collapsing the stop-loss floor to zero and completely disabling the LP value guard for affected bins.

## Finding Description
The lazy-decay model stores `(token0, token1, lastDecayTs)` per bin in `highWatermarks` and defers decay computation to `_checkAndUpdateWatermarks`, called from `afterSwap`. At swap time, `decayRate = cfg.decayPerSecondE8` is read from the live config and applied to `dt = block.timestamp - hwmS.lastDecayTs` for the full elapsed interval. When `executeOracleStopLossDecay` updates `decayPerSecondE8`, it does not touch any `highWatermarks` entry or advance `lastDecayTs`. The next swap therefore applies `rate_new` to a `dt` that includes the pre-update period. The correct computation is `hwm × (rate_old × (T_update − T_last) + rate_new × (T_swap − T_update)) / E8`; the actual computation is `hwm × rate_new × (T_swap − T_last) / E8`. When `rate_new × (T_swap − T_last) ≥ 1e8`, `_decayed` returns `0`, the floor `(0 × floorMultiplier) / E6 = 0` passes every metric check, and the stop-loss guard is silently disabled for the bin until a new high watermark is organically set by a subsequent swap at a higher value. The timelocked propose/execute flow does not mitigate this: the timelock only delays when the new rate takes effect, not whether the retroactive application occurs.

## Impact Explanation
**Higher-rate case (LP principal at risk):** The stop-loss floor collapses below the intended `drawdownE6` threshold. A swap that drains bin value past the intended drawdown limit is not reverted. LPs suffer a loss the guard was designed to prevent. In the PoC, the floor drops to exactly `0`, meaning any value loss passes unchecked — a complete failure of the protection mechanism for the affected bin.

**Lower-rate case (core pool functionality broken):** The watermark is under-decayed, raising the effective floor above the intended threshold. Legitimate swaps revert with `OracleStopLossTriggered` even though no actual value loss occurred, rendering the pool unusable until a new swap organically resets the watermarks to a higher value.

Both outcomes are direct consequences of a normal, timelocked administrative operation and constitute direct loss of user principal and broken core swap functionality, both within the allowed impact gate.

## Likelihood Explanation
Every legitimate decay-rate update triggers the bug. The two-step propose/execute flow with a timelock does not mitigate it. Any pool that has experienced a quiet period between the last watermark update and the decay-rate execution will exhibit the largest discrepancy, because `dt` is maximized. This is a realistic scenario: pools with infrequent trading or pools whose admins update parameters during low-activity windows. No attacker capability is required beyond the pool admin performing a routine parameter update.

## Recommendation
Before writing the new `decayPerSecondE8`, settle all active bin watermarks under the old rate and advance `lastDecayTs` to `block.timestamp`. Because iterating all bins on-chain is impractical, the cleanest fix is to store a rate-change checkpoint `(rate, effectiveFrom)` alongside the config and compute piecewise decay in `_decayed` as a two-segment sum: `old_rate × (effectiveFrom − lastDecayTs) + new_rate × (block.timestamp − effectiveFrom)`. This mirrors the correct fix for the analogous Predy bug: settle the current period under the old parameters before switching to the new ones.

## Proof of Concept
1. Pool initialized with `drawdownE6 = 50_000` (5%), `decayPerSecondE8 = 10`.
2. At `T=0`, a swap occurs: `highWatermarks[pool][0] = {token0: 1_000_000, token1: 1_000_000, lastDecayTs: 0}`.
3. Admin proposes and executes `decayPerSecondE8 = 1_000` at `T = 86_400`. No swap occurs in between.
4. At `T = 86_401`, a swap occurs. `dt = 86_401`. `factor = 1_000 × 86_401 = 86_401_000 ≥ 1e8`. `_decayed` returns `0`. Floor = `0`. The stop-loss check passes unconditionally regardless of actual value loss. The guard is completely disabled for this bin.

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L235-235)
```text
    uint256 decayRate = cfg.decayPerSecondE8;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L267-270)
```text
    BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
    uint256 dt = block.timestamp - hwmS.lastDecayTs;

    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
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
