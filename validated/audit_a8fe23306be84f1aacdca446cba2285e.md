Audit Report

## Title
Stop-Loss Guard Permanently Bypassed on First Swap Through Any Bin With Uninitialized Watermarks — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

`OracleValueStopLossExtension._checkAndUpdateWatermarks` passes the stored per-bin watermark through `_decayed`, which returns `0` immediately when `hwm == 0`. `_applyWatermark` then receives `hwm = 0` and evaluates `metric >= 0`, which is always true for `uint256`, unconditionally returning `breached = false`. Because `highWatermarks` is zero-initialized by default and `initialize` never sets per-bin watermarks, every bin in every newly deployed pool starts with `hwm = 0`, meaning any unprivileged swap can cause an arbitrarily large drawdown without triggering the stop-loss revert.

## Finding Description

`initialize` configures `drawdownE6`, `decayPerSecondE8`, and `timelock` but writes nothing to `highWatermarks`: [1](#0-0) 

Per-bin watermarks are only set via the admin-gated, timelocked `proposeOracleStopLossHighWatermarks` / `executeOracleStopLossHighWatermarks` flow, which is entirely separate from pool creation: [2](#0-1) 

In `_checkAndUpdateWatermarks`, the stored watermark is passed through `_decayed`. When `hwm == 0`, `_decayed` returns `0` immediately via the early-exit guard: [3](#0-2) 

`_applyWatermark` then receives `hwm = 0`. The condition `metric >= hwm` evaluates to `metric >= 0`, which is always true for `uint256`, so it returns `(metric, false)` — no breach — regardless of the actual drawdown magnitude: [4](#0-3) 

After the call, `hwmS.token0` and `hwmS.token1` are written to the post-drain metric values, permanently establishing the watermark at the already-drained level: [5](#0-4) 

The only existing guard that could short-circuit the check is `if (drawdown == 0) return` at line 217, which only fires when the stop-loss is entirely disabled — not when watermarks are zero. The `if (totalShares == 0) continue` guard at line 238 skips empty bins but not bins with uninitialized watermarks. [6](#0-5) 

## Impact Explanation

LPs who deposit into a pool using `OracleValueStopLossExtension` before any watermarks are initialized — the default state for every pool — receive zero stop-loss protection on the first swap through each bin. An attacker who executes that first swap can drain the bin's value by any amount, including 100%, without the `afterSwap` hook reverting. The watermark is then set to the post-drain value, so subsequent swaps are protected only from that lower baseline. This is a direct loss of LP principal with no on-chain recourse, violating the documented guarantee that "value per share at oracle marks cannot fall faster than drawdown." [7](#0-6) 

## Likelihood Explanation

Every pool using this extension starts with zero watermarks. The admin watermark-setting flow requires a separate proposal plus timelock execution after pool creation. During that window — or permanently if the admin never calls it — any public swap is the "first" swap for each bin and bypasses the guard entirely. No special privileges are required; any user can call the pool's swap function. [8](#0-7) 

## Recommendation

In `_checkAndUpdateWatermarks` (or `_applyWatermark`), treat `hwm == 0` as "guard not yet armed" and skip both the breach check and the watermark update, forcing the admin to explicitly initialize watermarks before the guard becomes active. Alternatively, require that `initialize` also accepts and stores initial per-bin watermarks, or add a check in `_checkAndUpdateWatermarks` that reverts (or skips) when `hwmS.token0 == 0 && hwmS.token1 == 0` and `drawdownE6 > 0`, preventing any swap until watermarks are set. [8](#0-7) 

## Proof of Concept

```solidity
// Foundry integration test sketch
function test_zeroWatermarkBypassStopLoss() public {
    // Pool initialized with 50% drawdown threshold, no watermarks set
    _initPool(address(mockPool), 500_000, 0, 0); // drawdownE6 = 50%

    uint128 price = uint128(Q64); // 1:1 price
    // Store bin with 1000 of each token, BIN_SHARES shares
    _storeBin(0, 1000, 1000, BIN_SHARES);

    // Simulate a swap that drops bin value by 50% (500 tokens remain)
    _storeBin(0, 500, 500, BIN_SHARES);

    // afterSwap is called; hwm=0 so _applyWatermark returns (metric, false)
    // This must NOT revert, demonstrating the bypass
    _exposeStopLoss(0, 0, price, true); // zeroForOne=true, 50% drop, should revert but doesn't

    // Watermark is now set to the post-drop value
    (uint256 hwm0,) = extension.currentHighWatermarks(address(mockPool), 0);
    assertGt(hwm0, 0); // watermark set to drained value, not original
}
```

The call to `_exposeStopLoss` does not revert despite the 50% drawdown exceeding the configured threshold, because `hwm = 0` causes `_applyWatermark` to return `breached = false` unconditionally. [9](#0-8)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L27-28)
```text
///      Watermarks decay linearly at decayPerSecondE8 (lazy, per bin). Guarantee: value per
///      share at oracle marks cannot fall faster than drawdown (one-time) + decay * t (ongoing).
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L56-62)
```text
    (uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
    _validateDrawdown(drawdownE6);
    _validateDecay(decayPerSecondE8);

    oracleStopLossConfig[pool] = PoolStopLossConfig({
      drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
    });
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L217-217)
```text
    if (drawdown == 0) return;
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L319-320)
```text
  function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
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
