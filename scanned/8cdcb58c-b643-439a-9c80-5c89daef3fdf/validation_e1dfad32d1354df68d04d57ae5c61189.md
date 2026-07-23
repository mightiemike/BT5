### Title
Stale Per-Bin High Watermarks After Full Liquidity Removal Cause Incorrect Stop-Loss Triggering, Permanently Blocking Swaps — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension` stores per-bin high watermarks (HWMs) keyed by `(pool, binIdx)`. When all liquidity is removed from a bin the HWMs are never cleared, because the `afterSwap` loop skips empty bins (`totalShares == 0`). When new liquidity is later added to the same bin index and a swap touches it, the extension compares the new (lower) per-share metrics against the stale (high) HWMs from the previous liquidity epoch. If the new metrics fall below `HWM × (1 − drawdown)`, the extension reverts the swap with `OracleStopLossTriggered`, permanently blocking all swaps through that bin until the HWMs decay to zero or the pool admin manually resets them through a timelocked proposal.

---

### Finding Description

`highWatermarks` is a nested mapping keyed by bin index (`int8`, range −128 to 127):

```solidity
mapping(address pool => mapping(int8 binIdx => BinHighWatermarks)) public highWatermarks;
``` [1](#0-0) 

The `afterSwap` hook iterates over every bin between the initial and final `curBinIdx` and skips bins with zero shares:

```solidity
if (totalShares == 0) continue;
``` [2](#0-1) 

Because the skip condition is `totalShares == 0`, the HWMs for a bin are **never written back to zero** when liquidity is fully removed. The `BinHighWatermarks.token0`, `token1`, and `lastDecayTs` fields all persist at their last-written values.

When new liquidity is deposited into the same bin index and a swap moves through it, `totalShares > 0` so the skip is not taken. `_checkAndUpdateWatermarks` then reads the stale HWMs:

```solidity
BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
uint256 dt = block.timestamp - hwmS.lastDecayTs;
(uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
if (breach0 && zeroForOne) {
    revert OracleStopLossTriggered(...);
}
``` [3](#0-2) 

`_applyWatermark` reports a breach whenever `metric < (hwm × floorMultiplier) / E6`:

```solidity
function _applyWatermark(uint256 metric, uint256 hwm, uint256 floorMultiplier)
    private pure returns (uint256 newHwm, bool breached)
{
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
    return (hwm, breached);
}
``` [4](#0-3) 

The decay helper floors at zero only when `ratePerSecondE8 × dt ≥ E8`:

```solidity
function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;
}
``` [5](#0-4) 

If `decayPerSecondE8 == 0` (a valid configuration meaning "no decay"), the stale HWMs never shrink and the stop-loss fires on every swap through the reused bin indefinitely. Even with non-zero decay, the window during which the stop-loss fires incorrectly is `drawdown × E8 / ratePerSecondE8` seconds (e.g., ~2 days for a 10 % drawdown at the example rate of 58 units/s).

The only remediation paths are:
1. Wait for HWMs to decay past the drawdown floor (impossible if `decayPerSecondE8 == 0`).
2. Pool admin calls `proposeOracleStopLossHighWatermarks` + `executeOracleStopLossHighWatermarks` after the configured timelock elapses. [6](#0-5) 

---

### Impact Explanation

All swaps that route through the affected bin revert with `OracleStopLossTriggered`. This renders the pool's swap functionality unusable for any trade that crosses or lands in the reused bin. LPs can still remove liquidity (the stop-loss extension does not implement `beforeRemoveLiquidity` or `afterRemoveLiquidity`), so principal is not directly seized, but the pool's core swap flow is broken and protocol fee revenue is zeroed for the duration. When `decayPerSecondE8 == 0` the outage is permanent until admin intervention, satisfying the "unusable swap flows" impact gate.

---

### Likelihood Explanation

The trigger is a normal market lifecycle event: an LP removes all liquidity from a bin (e.g., after a price move takes the oracle outside the bin's range) and a different LP later re-adds liquidity to the same bin index at a lower per-share value. No privileged action is required to trigger the condition; the attacker role is simply any LP or market participant operating under ordinary conditions. Pools with a small number of active bins (e.g., a single-bin concentrated pool) are especially susceptible because bin-index reuse is nearly certain over time.

---

### Recommendation

Clear or reset the HWMs for a bin whenever its `totalShares` transitions from non-zero to zero. One approach is to add an `afterRemoveLiquidity` hook that checks whether the bin is now empty and, if so, deletes `highWatermarks[pool_][binIdx]`. Alternatively, track a per-bin "epoch" counter that is incremented on each full-drain event and include it in the HWM mapping key, so stale HWMs from a prior epoch are never compared against metrics from the current epoch — directly mirroring the external report's recommendation to key state by identity rather than by reusable index.

---

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension` configured with `drawdownE6 = 100_000` (10 %), `decayPerSecondE8 = 0` (no decay), `timelock = 7 days`.
2. LP A adds liquidity to bin 0. Several swaps occur, driving `highWatermarks[pool][0].token0` to a high value H.
3. LP A removes all liquidity from bin 0. `totalShares[0]` drops to 0; HWMs remain at H.
4. LP B adds liquidity to bin 0 at the current (lower) oracle price. `totalShares[0]` is now non-zero.
5. Any swap that touches bin 0 calls `afterSwap`. `_checkAndUpdateWatermarks` reads the stale HWM H, computes `metricT0 < H × 0.9`, sets `breach0 = true`, and reverts with `OracleStopLossTriggered`.
6. Because `decayPerSecondE8 == 0`, every subsequent swap through bin 0 also reverts. The pool's swap functionality is permanently broken until the admin completes a 7-day timelocked HWM reset.

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L40-40)
```text
  mapping(address pool => mapping(int8 binIdx => BinHighWatermarks)) public highWatermarks;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L157-177)
```text
  function proposeOracleStopLossHighWatermarks(address pool_, int8 binIdx, uint104 newHwmToken0, uint104 newHwmToken1)
    external
    onlyPoolAdmin(pool_)
  {
    _requireInitialized(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    pendingHighWatermark[pool_] =
      PendingHighWatermarks({token0: newHwmToken0, token1: newHwmToken1, binIdx: binIdx, executeAfter: executeAfter});
    emit OracleStopLossHighWatermarkProposed(pool_, binIdx, newHwmToken0, newHwmToken1, executeAfter);
  }

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L237-238)
```text
      uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
      if (totalShares == 0) continue;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L267-278)
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
