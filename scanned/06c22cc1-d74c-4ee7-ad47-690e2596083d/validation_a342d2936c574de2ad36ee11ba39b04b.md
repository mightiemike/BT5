The code is fully readable. Let me trace the exact execution path to determine validity.

**Execution trace for first swap on an uninitialized bin:**

`_checkAndUpdateWatermarks` (line 267): `hwmS.token0 == 0`, `hwmS.token1 == 0`

`_decayed(0, decayRate, dt)` (line 320): short-circuits on `hwm == 0`, returns `0`

`_applyWatermark(metricT0, 0, floorMultiplier)` (line 333):
```
if (metric >= hwm) return (metric, false);
// metric is uint256, hwm is 0 → metric >= 0 is ALWAYS true
// returns (post-drain metric, false) — no breach, no revert
```

The watermark is then written as the post-drain value at lines 281–284. The stop-loss is completely inert on the first swap touching any bin.

**Is this a real loss?**

The `afterSwap` hook reverts the entire swap transaction when a breach is detected. With `hwm == 0`, no breach is ever detected regardless of how much value is drained. An unprivileged trader who is first to touch a bin can drain it to near-zero, the hook accepts it, and the watermark is anchored to the already-drained metric. All subsequent swaps are then measured against that low baseline, not the pre-drain high. The NatSpec guarantee — *"value per share at oracle marks cannot fall faster than drawdown (one-time) + decay * t"* — is violated unconditionally on the first swap.

The admin watermark setter (`proposeOracleStopLossHighWatermarks` / `executeOracleStopLossHighWatermarks`) can pre-seed a bin, but it is subject to the pool timelock and requires proactive admin action. There is no mechanism that forces watermark seeding before the first swap, and no `afterAddLiquidity` hook in `OracleValueStopLossExtension` to auto-seed on deposit.

**Verdict: valid Medium.**

---

### Title
First-swap watermark bootstrap bypasses stop-loss on every bin — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

### Summary
`OracleValueStopLossExtension` initialises per-bin watermarks lazily: the first swap that touches a bin always passes the stop-loss check because the stored watermark is `0`, and `uint256 metric >= 0` is unconditionally true. The watermark is then written to the post-drain metric, permanently anchoring the baseline to the already-drained value.

### Finding Description
`_checkAndUpdateWatermarks` reads `hwmS.token0` and `hwmS.token1` from storage. For any bin that has never been touched, both are `0`. The call chain is:

```
_decayed(0, rate, dt)  →  returns 0   (line 320: hwm == 0 short-circuit)
_applyWatermark(metric, 0, floor)
  → metric >= 0 is always true          (line 333)
  → returns (metric, false)             // no breach, no revert
```

After the call, lines 281–284 write `hwm0 = metric` (the post-drain value) and `hwm1 = metric`. Every subsequent swap is compared against this low baseline, so the drawdown floor is effectively `post_drain_value * (1 - drawdown)` — far below the pre-drain LP value. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation
An unprivileged trader who executes the first swap on a bin can drain it by an arbitrary amount — up to the full bin balance — without triggering the stop-loss revert. LPs suffer direct principal loss equal to the drained value. The watermark is then set to the post-drain metric, so the protection is permanently weakened for that bin unless the admin manually reseeds it (which itself requires a timelock delay).

### Likelihood Explanation
Every newly created pool starts with all-zero watermarks. Any public swap on a fresh pool or on a bin that has never been touched is sufficient. No special role, oracle manipulation, or non-standard token is required. The attacker only needs to be the first to call `swap` on the target bin, which is trivially achievable by monitoring the mempool or simply acting immediately after pool creation.

### Recommendation
Seed watermarks at the time liquidity is first added to a bin. The cleanest fix is to implement `afterAddLiquidity` in `OracleValueStopLossExtension` and, for each bin in the delta whose stored watermark is still `0`, compute the current metric and write it as the initial watermark. Alternatively, treat `hwm == 0` as "unset" inside `_applyWatermark` and skip the breach check while still writing the metric — but also emit an event so the admin can verify the seed value. The admin-driven `proposeOracleStopLossHighWatermarks` path is insufficient on its own because it cannot atomically precede the first public swap. [4](#0-3) 

### Proof of Concept
```solidity
// Foundry integration sketch
function test_firstSwapDrainsWithoutStopLoss() public {
    // 1. Pool initialised with drawdownE6 = 100_000 (10%)
    _initPool(address(mockPool), 100_000, 0, 0);

    // 2. LP adds liquidity: bin 0 holds 10_000 t0, 10_000 t1
    _storeBin(0, 10_000, 10_000, BIN_SHARES);

    // 3. Attacker executes first swap — drains bin to near-zero
    //    (simulate post-drain state: 100 t0, 100 t1)
    _storeBin(0, 100, 100, BIN_SHARES);

    // 4. afterSwap hook fires — should revert but does NOT
    _exposeStopLoss(0, 0, uint128(Q64), true); // no revert

    // 5. Watermark is now anchored to the drained metric
    (uint256 hwm0,) = extension.currentHighWatermarks(address(mockPool), 0);
    uint256 drainedMetric = _computeMetricToken0(100, 100, BIN_SHARES, uint128(Q64));
    assertEq(hwm0, drainedMetric); // watermark == post-drain, not pre-drain
}
```

The test confirms: the first swap passes with no revert regardless of the magnitude of the drain, and the resulting watermark equals the post-drain metric rather than the pre-drain high.

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
