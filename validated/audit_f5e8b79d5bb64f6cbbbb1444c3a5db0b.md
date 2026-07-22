### Title
`lastDecayTs` Not Updated for Zero-Share Bins Causes Stop-Loss Watermark to Over-Decay and Be Bypassed — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss()` skips `_checkAndUpdateWatermarks()` for any bin where `totalShares == 0`. This means `lastDecayTs` is never advanced for that bin during the zero-share period. When liquidity is later re-added and a swap touches the bin, the elapsed time `dt` is computed against the stale `lastDecayTs`, causing the watermark to decay to zero in a single call. A zero watermark makes `_applyWatermark` unconditionally return `(metric, false)` — no breach — so the stop-loss guard is silently bypassed for that swap, allowing value-draining swaps to complete without reversion.

---

### Finding Description

In `_afterSwapOracleStopLoss`, the extension iterates over every bin in the swap range and skips empty bins:

```solidity
// OracleValueStopLossExtension.sol lines 236-242
for (uint256 i = 0; i < count; i++) {
    uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
    if (totalShares == 0) continue;          // ← lastDecayTs never updated
    ...
    _checkAndUpdateWatermarks(pool_, binIdxs[i], ...);
}
```

`_checkAndUpdateWatermarks` is the only place `lastDecayTs` is written:

```solidity
// lines 267-284
BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
uint256 dt = block.timestamp - hwmS.lastDecayTs;
...
hwmS.lastDecayTs = uint32(block.timestamp);   // ← only reached when totalShares > 0
```

The decay function zeroes the watermark once `ratePerSecondE8 * dt >= 1e8`:

```solidity
// lines 319-324
uint256 factor = ratePerSecondE8 * dt;
if (factor >= E8) return 0;
```

At the documented example rate of 58 per second (≈ 5 %/day), this threshold is crossed after `1e8 / 58 ≈ 19.95 days`. Once the watermark is zero, `_applyWatermark` always returns `(metric, false)`:

```solidity
// lines 328-335
function _applyWatermark(uint256 metric, uint256 hwm, uint256 floorMultiplier)
    private pure returns (uint256 newHwm, bool breached)
{
    if (metric >= hwm) return (metric, false);   // metric >= 0 is always true
    ...
}
```

The stop-loss never fires, and the watermark is silently reset to the current (potentially already-drained) metric value.

---

### Impact Explanation

The `OracleValueStopLossExtension` is the primary on-chain guard that reverts swaps when per-share value in a bin falls below the configured drawdown floor. Bypassing it means:

- A swap that would drain LP value beyond the configured `drawdownE6` threshold completes without reversion.
- The watermark is silently reset to the post-drain metric, so subsequent swaps are also checked against the lower baseline.
- LP principal deposited into the re-liquified bin is exposed to the full oracle-price risk the stop-loss was designed to cap.

This is a direct loss of LP principal above the protocol-configured drawdown threshold.

---

### Likelihood Explanation

The preconditions are realistic and require no privileged access:

1. **Bin reaches zero shares** — normal LP behavior; any bin can be fully withdrawn at any time.
2. **~20 days elapse** — a common gap between LP cycles, especially for less-active bins or during market stress when LPs exit.
3. **New liquidity is added** — any allowed depositor (or all depositors if `allowAllDepositors` is set) can trigger this.
4. **A swap touches the bin** — any swap that crosses the bin range triggers `afterSwap`.

No oracle manipulation is required; the bypass occurs purely from the stale `lastDecayTs` regardless of whether the oracle price is correct or adversarial.

---

### Recommendation

Update `lastDecayTs` for zero-share bins without performing the breach check or watermark ratchet. The simplest fix is to advance the decay clock even when skipping the metric computation:

```diff
for (uint256 i = 0; i < count; i++) {
    uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
    if (totalShares == 0) {
+       // Advance the decay clock so stale dt cannot accumulate.
+       highWatermarks[pool_][binIdxs[i]].lastDecayTs = uint32(block.timestamp);
        continue;
    }
    (uint104 t0, uint104 t1,,,) = PoolStateLibrary._decodeBinState(states[i]);
    (uint256 metricT0, uint256 metricT1) = _metrics(t0, t1, totalShares, minShares, midPriceX64);
    _checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1, floorMultiplier, decayRate, zeroForOne);
}
```

This mirrors the fix recommended in the reference report: update the timestamp regardless of whether the primary action (minting / breach check) is taken.

---

### Proof of Concept

1. Pool is deployed with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5%), `decayPerSecondE8 = 58` (≈5%/day), `timelock = 0`.
2. LP Alice adds liquidity to bin 0. Admin sets watermarks: `hwm0 = 1000`, `hwm1 = 1000`, `lastDecayTs = T0`.
3. Alice removes all liquidity. `totalShares[0] = 0`.
4. 20 days pass (`dt = 1_728_000 s`). Swaps occur on other bins; bin 0 is skipped each time. `lastDecayTs` for bin 0 remains `T0`.
5. Bob adds liquidity to bin 0. `totalShares[0] > 0`.
6. A swap occurs that crosses bin 0. `_checkAndUpdateWatermarks` is called.
   - `dt = block.timestamp - T0 = 1_728_000`
   - `factor = 58 * 1_728_000 = 100_224_000 > 1e8` → `_decayed(1000, 58, 1_728_000) = 0`
   - `_applyWatermark(metricT0, 0, 950_000)` → `metricT0 >= 0` → returns `(metricT0, false)`
7. No `OracleStopLossTriggered` revert. The swap completes. Bob's deposited value is drained beyond the 5% drawdown floor with no on-chain protection. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L236-242)
```text
    for (uint256 i = 0; i < count; i++) {
      uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
      if (totalShares == 0) continue;
      (uint104 t0, uint104 t1,,,) = PoolStateLibrary._decodeBinState(states[i]);
      (uint256 metricT0, uint256 metricT1) = _metrics(t0, t1, totalShares, minShares, midPriceX64);
      _checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1, floorMultiplier, decayRate, zeroForOne);
    }
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
