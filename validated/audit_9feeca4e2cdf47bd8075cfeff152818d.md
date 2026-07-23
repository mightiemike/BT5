Audit Report

## Title
`lastDecayTs` Not Updated for Zero-Share Bins Allows Stop-Loss Watermark to Fully Decay and Be Bypassed — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

In `_afterSwapOracleStopLoss`, bins with `totalShares == 0` are skipped via `continue`, which prevents `_checkAndUpdateWatermarks` from advancing `lastDecayTs` for those bins. After sufficient idle time, the accumulated `dt` causes `_decayed` to return `0` for the stored watermark. A zero watermark causes `_applyWatermark` to unconditionally return `(metric, false)` — no breach — silently bypassing the stop-loss for the first swap after liquidity is re-added to the bin.

## Finding Description

The loop in `_afterSwapOracleStopLoss` skips zero-share bins entirely:

```solidity
// Lines 236-242
for (uint256 i = 0; i < count; i++) {
    uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
    if (totalShares == 0) continue;   // _checkAndUpdateWatermarks never called
    ...
    _checkAndUpdateWatermarks(pool_, binIdxs[i], ...);
}
``` [1](#0-0) 

`lastDecayTs` is only written inside `_checkAndUpdateWatermarks` (line 284) and the admin-gated `executeOracleStopLossHighWatermarks` (line 174). During normal swap execution, it is never advanced for a zero-share bin:

```solidity
// Lines 267-268, 284
BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
uint256 dt = block.timestamp - hwmS.lastDecayTs;
...
hwmS.lastDecayTs = uint32(block.timestamp);  // only reached when totalShares > 0
``` [2](#0-1) 

Once `ratePerSecondE8 * dt >= 1e8`, `_decayed` returns `0`:

```solidity
// Lines 321-322
uint256 factor = ratePerSecondE8 * dt;
if (factor >= E8) return 0;
``` [3](#0-2) 

With `hwm == 0`, `_applyWatermark` always takes the first branch (`metric >= 0` is always true for `uint256`) and returns `(metric, false)` — no breach:

```solidity
// Line 333
if (metric >= hwm) return (metric, false);
``` [4](#0-3) 

Neither `breach0` nor `breach1` can be `true`, so neither `OracleStopLossTriggered` revert fires. The watermark is silently reset to the current (potentially already-drained) metric value, and subsequent swaps are checked against the lower baseline.

## Impact Explanation

`OracleValueStopLossExtension` is the primary on-chain guard that reverts swaps when per-share bin value falls below the configured `drawdownE6` floor. Bypassing it means:

- A swap that drains LP value beyond the configured drawdown threshold completes without reversion.
- The watermark is silently reset to the post-drain metric, so subsequent swaps are also checked against the lower baseline.
- LP principal deposited into the re-liquified bin is exposed to the full oracle-price risk the stop-loss was designed to cap.

This is a direct loss of LP principal above the protocol-configured drawdown threshold, matching the "Critical/High/Medium direct loss of user principal" allowed impact gate.

## Likelihood Explanation

All preconditions are reachable by unprivileged actors with no oracle manipulation:

1. **Bin reaches zero shares** — any LP can fully withdraw at any time; normal behavior.
2. **~20 days elapse** — at the documented example rate of 58/s, the threshold is `1e8 / 58 ≈ 1,724,138 s ≈ 19.95 days`; a common gap between LP cycles.
3. **New liquidity is added** — any allowed depositor (or all depositors if `allowAllDepositors` is set) can trigger this.
4. **A swap crosses the bin** — any swap in the bin's price range triggers `afterSwap`.

No privileged access, no oracle manipulation, and no non-standard token behavior is required.

## Recommendation

Advance `lastDecayTs` for zero-share bins without performing the breach check or watermark ratchet:

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

This ensures the decay clock is always current regardless of whether the bin has active liquidity, preventing unbounded `dt` accumulation.

## Proof of Concept

1. Deploy pool with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5%), `decayPerSecondE8 = 58` (~5%/day), `timelock = 0`.
2. LP Alice adds liquidity to bin 0. Admin sets watermarks: `hwm0 = 1000`, `hwm1 = 1000`, `lastDecayTs = T0`.
3. Alice removes all liquidity. `totalShares[bin 0] = 0`.
4. 20 days pass (`dt = 1,728,000 s`). Swaps occur on other bins; bin 0 is skipped each time. `lastDecayTs` for bin 0 remains `T0`.
5. Bob adds liquidity to bin 0. `totalShares[bin 0] > 0`.
6. A swap crosses bin 0. `_checkAndUpdateWatermarks` is called:
   - `dt = block.timestamp - T0 = 1,728,000`
   - `factor = 58 * 1,728,000 = 100,224,000 > 1e8` → `_decayed(1000, 58, 1_728_000) = 0`
   - `_applyWatermark(metricT0, 0, 950_000)` → `metricT0 >= 0` → returns `(metricT0, false)`
7. No `OracleStopLossTriggered` revert. The swap completes. Bob's deposited value is drained beyond the 5% drawdown floor with no on-chain protection. Watermark is silently reset to the current drained metric.

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L267-284)
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
