Let me read the full `_metrics`, `_clampMetric`, `_applyWatermark`, and `_checkAndUpdateWatermarks` logic carefully, plus the existing test for this edge case.

The code is fully readable. I can now trace the complete execution path.

---

### Title
Clamped Sentinel Value Written to High Watermark Permanently Triggers Stop-Loss After Balance Normalisation — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

### Summary

`_clampMetric` returns `METRIC_MAX = type(uint104).max` as a sentinel for arithmetic overflow, but `_applyWatermark` treats that sentinel as a legitimate high-water value and ratchets `hwmS.token0` / `hwmS.token1` up to `type(uint104).max`. Once the watermark is pinned at `type(uint104).max`, every subsequent swap whose metric is a normal (non-overflowing) value falls below the drawdown floor, causing the stop-loss to revert every swap in that direction until a pool admin manually resets the watermark through the timelocked admin flow.

### Finding Description

**Overflow path in `_metrics`** [1](#0-0) 

`shares` is floored at `minimalMintableLiquidity` (e.g. 1 000). With `t0 = type(uint104).max ≈ 2^104`:

```
t0ps = type(uint104).max * 1e6 / 1000 = type(uint104).max * 1000
```

This already exceeds `METRIC_MAX = type(uint104).max`, so `_clampMetric` returns the sentinel `type(uint104).max`. [2](#0-1) 

The existing test explicitly confirms this outcome: [3](#0-2) 

**Sentinel written to storage as a real watermark**

`_applyWatermark` ratchets up whenever `metric >= hwm`: [4](#0-3) 

With `metric = type(uint104).max` and `hwm = 0` (initial state), the condition `metric >= hwm` is true, so it returns `(type(uint104).max, false)`. The caller then writes this to storage: [5](#0-4) 

**Permanent stop-loss on subsequent swaps**

After the watermark is pinned at `type(uint104).max`, any future swap whose bin balances have returned to normal (e.g. after LP withdrawals) produces a normal metric, say `metricT0 = 1 000`. Then:

```
_applyWatermark(1_000, type(uint104).max, 950_000)
  → metric < hwm                          // true
  → breached = 1_000 < (type(uint104).max * 950_000 / 1e6)
  → breached = 1_000 < ~0.95 * 2^104     // true → revert
``` [6](#0-5) 

Every swap in the affected direction reverts with `OracleStopLossTriggered` until the pool admin resets the watermark via the timelocked `proposeOracleStopLossHighWatermarks` / `executeOracleStopLossHighWatermarks` flow. [7](#0-6) 

### Impact Explanation

All swaps in the affected direction (`zeroForOne` if `metricT0` is corrupted, `!zeroForOne` if `metricT1` is corrupted) are permanently blocked until admin intervention. This is broken core pool functionality: the swap flow is completely unusable for one or both directions. During the timelock window, no remediation is possible on-chain.

### Likelihood Explanation

The condition is: `t0 * METRIC_SCALE / minShares > type(uint104).max`, i.e. `t0 / minShares > ~2^84`. This is reachable for any pool whose `minimalMintableLiquidity` is small and whose bin holds a large raw balance of a cheap token (e.g. a meme token with 18 decimals where `type(uint104).max / 1e18 ≈ 20 trillion` tokens). No special attacker action is required beyond triggering any swap through such a pool — the corruption happens on the first `afterSwap` call. The existing test `test_dustShares_flooredByMinLiquidity_noRevert` already exercises the exact state that produces `hwm = type(uint104).max`, but does not assert subsequent swap behaviour.

### Recommendation

In `_checkAndUpdateWatermarks`, skip the watermark update (and treat the metric as non-comparable) when the computed metric equals `METRIC_MAX`:

```solidity
if (metricT0 < METRIC_MAX) {
    (uint256 hwm0, bool breach0) = _applyWatermark(...);
    ...
    hwmS.token0 = uint104(hwm0);
}
```

Alternatively, propagate a boolean `clamped` flag from `_metrics` and skip both the breach check and the watermark write for any clamped metric, so the sentinel never enters storage.

### Proof of Concept

```solidity
// 1. Pool bin has extreme balances (realistic for cheap tokens)
_storeBin(0, type(uint104).max, type(uint104).max, 1);
_configure(50_000, 0); // 5% drawdown, no decay

// 2. First swap: watermark ratchets to type(uint104).max
_exposeStopLoss(0, 0, uint128(Q64), false);
(uint256 hwm0,) = extension.currentHighWatermarks(address(mockPool), 0);
assertEq(hwm0, type(uint104).max); // confirmed by existing test

// 3. Balances normalise (LP withdraws), next swap has normal metric
_storeBin(0, 1000, 1000, BIN_SHARES);

// 4. Any zeroForOne swap now permanently reverts
vm.expectRevert(
    abi.encodeWithSelector(IOracleValueStopLossExtension.OracleStopLossTriggered.selector, ...)
);
_exposeStopLoss(0, 0, uint128(Q64), true); // blocked forever
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L156-177)
```text
  /// @notice Propose per-bin high watermarks; applied after the pool timelock via execute.
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L251-255)
```text
    uint256 shares = totalShares < minShares ? minShares : totalShares;
    uint256 t0ps = Math.mulDiv(uint256(t0), METRIC_SCALE, shares);
    uint256 t1ps = Math.mulDiv(uint256(t1), METRIC_SCALE, shares);
    metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L270-278)
```text
    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
    }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L280-284)
```text
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token0 = uint104(hwm0);
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token1 = uint104(hwm1);
    hwmS.lastDecayTs = uint32(block.timestamp);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L313-316)
```text
  /// @dev Clamp pathological oracle-price blowups; normal bins with uint104 balances stay below this.
  function _clampMetric(uint256 metric) private pure returns (uint256) {
    return metric > METRIC_MAX ? METRIC_MAX : metric;
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

**File:** metric-periphery/test/extensions/OracleValueStopLossSubExtension.t.sol (L495-505)
```text
  function test_dustShares_flooredByMinLiquidity_noRevert() public {
    // Dust shares are floored at minimalMintableLiquidity; max uint104 balances clamp to uint104.max.
    _storeBin(0, type(uint104).max, type(uint104).max, 1);
    _configure(50_000, 0);

    _exposeStopLoss(0, 0, uint128(Q64), false);

    (uint256 hwm0, uint256 hwm1) = extension.currentHighWatermarks(address(mockPool), 0);
    assertEq(hwm0, type(uint104).max);
    assertEq(hwm1, type(uint104).max);
  }
```
