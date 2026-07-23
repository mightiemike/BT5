### Title
`executeOracleStopLossDecay` Does Not Settle Per-Bin High Watermarks Before Updating `decayPerSecondE8`, Causing the New Rate to Retroactively Apply to the Elapsed Period — (`File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`executeOracleStopLossDecay` writes a new `decayPerSecondE8` directly into `oracleStopLossConfig` without first applying the current (old) decay to the stored per-bin `highWatermarks`. Because the decay is computed lazily at swap time using `block.timestamp - hwmS.lastDecayTs`, the new rate retroactively covers the entire elapsed period since the last swap, not just the period after the update. This is the exact structural analog of the Predy `updateIRMParams` bug: a parameter governing an ongoing time-integral calculation is replaced without first "settling" the accumulated state under the old parameter.

---

### Finding Description

The `OracleValueStopLossExtension` uses a lazy-decay model. Per-bin watermarks are stored as raw peak values alongside a `lastDecayTs` timestamp. Decay is applied on-demand inside `_checkAndUpdateWatermarks`, called from `afterSwap`:

```solidity
// OracleValueStopLossExtension.sol:268-284
uint256 dt = block.timestamp - hwmS.lastDecayTs;
(uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
...
hwmS.lastDecayTs = uint32(block.timestamp);
```

`_decayed` computes:

```solidity
// OracleValueStopLossExtension.sol:319-324
function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;
}
```

The `decayRate` used here is read from `cfg.decayPerSecondE8` at swap time. When the admin executes a decay update:

```solidity
// OracleValueStopLossExtension.sol:139-147
function executeOracleStopLossDecay(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingDecayExecuteAfter == 0) revert OracleStopLossNoPendingDecay(pool_);
    _requireElapsed(sched.pendingDecayExecuteAfter);
    uint32 decay = sched.pendingDecayPerSecondE8;
    oracleStopLossConfig[pool_].decayPerSecondE8 = decay;   // ← new rate written
    (sched.pendingDecayPerSecondE8, sched.pendingDecayExecuteAfter) = (0, 0);
    emit OracleStopLossDecaySet(pool_, decay);
}
```

No bin's `highWatermarks` entry is touched. `hwmS.lastDecayTs` is not advanced to `block.timestamp`. The stored HWM values still reflect the peak from the last swap.

At the next swap, `dt = T_swap − T_last` spans the entire gap — including the period before the rate change — but the full `dt` is multiplied by `rate_new`. The correct computation would be:

```
decayed_correct = hwm − hwm × (rate_old × (T_update − T_last) + rate_new × (T_swap − T_update)) / E8
decayed_actual  = hwm − hwm × (rate_new × (T_swap − T_last)) / E8
```

When `rate_new > rate_old`, `decayed_actual < decayed_correct`: the watermark is deflated more than it should be, so the stop-loss floor (`decayed_hwm × (1 − drawdown) / E6`) is lower than intended. A value drop that should have triggered the guard passes through silently.

When `rate_new < rate_old`, `decayed_actual > decayed_correct`: the watermark is higher than it should be, so the floor is higher and the guard fires on a swap that should have been permitted.

---

### Impact Explanation

**LP principal at risk (higher-rate case):** The stop-loss extension exists specifically to protect LP funds from oracle-price-driven value extraction. When `decayPerSecondE8` is increased, the retroactive over-decay lowers the effective floor below the intended `drawdownE6` threshold. A swap that drains bin value past the intended drawdown limit is not reverted. LPs suffer a loss that the guard was designed to prevent.

**Pool functionality broken (lower-rate case):** When `decayPerSecondE8` is decreased, the retroactive under-decay raises the effective floor above the intended threshold. Legitimate swaps revert with `OracleStopLossTriggered` even though no actual value loss occurred, rendering the pool unusable until a new swap naturally resets the watermarks.

Both outcomes are direct consequences of a normal, timelocked administrative operation, not a malicious setup.

---

### Likelihood Explanation

Every legitimate decay-rate update triggers the bug. The two-step propose/execute flow with a timelock does not mitigate it — the timelock only delays when the new rate takes effect, not whether the retroactive application occurs. Any pool that has experienced a quiet period (no swaps) between the last watermark update and the decay-rate execution will exhibit the largest discrepancy, because `dt` is maximized. This is a realistic scenario: pools with infrequent trading or pools whose admins update parameters during low-activity windows.

---

### Recommendation

Before writing the new `decayPerSecondE8`, apply the current decay to every active bin's watermarks using the old rate and advance `lastDecayTs` to `block.timestamp`. Because iterating all bins on-chain is impractical, the cleanest fix is to record the rate change as a checkpoint and compute piecewise decay at read time (two-segment: old rate × elapsed-before-update + new rate × elapsed-after-update). A simpler but sufficient alternative is to require that `executeOracleStopLossDecay` also resets `lastDecayTs` for all bins to `block.timestamp` by storing a global "rate-change epoch" alongside the new rate, and adjusting `_decayed` to split the interval at that epoch.

Minimum viable patch: store `(rate, effectiveFrom)` pairs and compute decay as a two-segment sum in `_decayed`. This mirrors the correct fix for the Predy bug: settle the current period under the old parameters before switching to the new ones.

---

### Proof of Concept

**Setup:**
- Pool initialized with `drawdownE6 = 50_000` (5%), `decayPerSecondE8 = 10` (slow decay).
- At `T=0`, a swap occurs. `highWatermarks[pool][0] = {token0: 1_000_000, token1: 1_000_000, lastDecayTs: 0}`.
- Admin proposes and executes `decayPerSecondE8 = 1_000` (fast decay) at `T = 86_400` (1 day later). No swap occurs between `T=0` and `T=86_400`.

**Correct behavior at next swap `T = 86_401`:**
- Old rate applies for `[0, 86_400]`: `decay_old = 10 × 86_400 / 1e8 = 0.00864` → HWM after old period ≈ `991_360`
- New rate applies for `[86_400, 86_401]`: `decay_new = 1_000 × 1 / 1e8 = 0.00001` → HWM ≈ `991_350`
- Floor = `991_350 × 950_000 / 1_000_000 ≈ 941_782`

**Actual behavior (buggy):**
- New rate applies for full `dt = 86_401`: `factor = 1_000 × 86_401 = 86_401_000`; since `86_401_000 >= 1e8`, `_decayed` returns `0`.
- Floor = `0 × 950_000 / 1_000_000 = 0`.
- The stop-loss check always passes regardless of actual value loss. The guard is completely disabled for this bin until a new high watermark is set. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L215-235)
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
