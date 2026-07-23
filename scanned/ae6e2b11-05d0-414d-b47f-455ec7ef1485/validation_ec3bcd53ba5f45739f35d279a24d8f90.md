The code trace is complete. Let me analyze the exact logic path.

**Step-by-step trace for a bin with `hwmS.token0 = 0` (default/uninitialized):**

1. `_decayed(0, decayRate, dt)` → returns `0` because of the early-exit at line 320: `if (... || hwm == 0) return hwm`
2. `_applyWatermark(metricT0_dropped, 0, floorMultiplier)` → line 333: `if (metric >= hwm)` → `metricT0_dropped >= 0` is **always true** for `uint256` → returns `(metricT0_dropped, false)` — **no breach**
3. `hwmS.token0 = uint104(metricT0_dropped)` — watermark is now set to the post-drop value

The stop-loss **never triggers** on the first swap through any bin whose watermark was never initialized.

---

### Title
Stop-Loss Guard Permanently Bypassed on First Swap Through Any Bin With Uninitialized Watermarks — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._checkAndUpdateWatermarks` treats a zero stored watermark as "no prior high," causing `_applyWatermark` to unconditionally return `breached = false` for any metric value. Because `highWatermarks` is zero-initialized by default and the `initialize` function never sets per-bin watermarks, every bin in every newly deployed pool starts with `hwm = 0`. An attacker who executes the first swap through a bin can cause an arbitrarily large drawdown without triggering the stop-loss revert.

---

### Finding Description

`OracleValueStopLossExtension.initialize` configures `drawdownE6`, `decayPerSecondE8`, and `timelock`, but does **not** set any per-bin `highWatermarks`. [1](#0-0) 

Per-bin watermarks are only set via the admin-gated, timelocked `proposeOracleStopLossHighWatermarks` / `executeOracleStopLossHighWatermarks` flow, which is optional and separate from pool creation. [2](#0-1) 

In `_checkAndUpdateWatermarks`, the stored watermark is passed through `_decayed`. When `hwm == 0`, `_decayed` returns `0` immediately: [3](#0-2) 

`_applyWatermark` then receives `hwm = 0`. The guard condition `metric >= hwm` evaluates to `metric >= 0`, which is **always true** for `uint256`. It returns `(metric, false)` — no breach — regardless of how large the drawdown is: [4](#0-3) 

After the call, `hwmS.token0` is written to the post-drop metric value, so the watermark is permanently established at the already-drained level: [5](#0-4) 

---

### Impact Explanation

LPs who deposit into a pool using `OracleValueStopLossExtension` before any watermarks are initialized (the default state) receive **zero stop-loss protection** on the first swap through each bin. An attacker who executes that first swap can drain the bin's value by any amount — including 100% — without the `afterSwap` hook reverting. The watermark is then set to the post-drain value, so subsequent swaps are protected only from that lower baseline. This is a direct loss of LP principal with no on-chain recourse. [6](#0-5) 

---

### Likelihood Explanation

Every pool using this extension starts with zero watermarks. The admin watermark-setting flow requires a separate proposal + timelock execution after pool creation. During that window — or permanently if the admin never calls it — any public swap is the "first" swap for each bin and bypasses the guard entirely. No special privileges are required; any user can call the pool's swap function. [7](#0-6) 

---

### Recommendation

In `_applyWatermark` (or in `_checkAndUpdateWatermarks`), treat `hwm == 0` as "guard not yet armed" and **skip the breach check but also skip the watermark update**, forcing the admin to explicitly initialize watermarks before the guard becomes active. Alternatively, require that `initialize` also accepts and stores initial per-bin watermarks, or add a check in `_checkAndUpdateWatermarks` that reverts (or skips) when `hwmS.token0 == 0 && hwmS.token1 == 0` and `drawdownE6 > 0`, preventing any swap until watermarks are set. [8](#0-7) 

---

### Proof of Concept

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

The call to `_exposeStopLoss` does **not** revert despite the 50% drawdown exceeding the configured threshold, because `hwm = 0` causes `_applyWatermark` to return `breached = false` unconditionally. [9](#0-8)

### Citations

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L185-203)
```text
  function afterSwap(
    address,
    address,
    bool zeroForOne,
    int128,
    uint128,
    uint256 packedSlot0Initial,
    uint256 packedSlot0Final,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    int128,
    int128,
    uint256,
    bytes calldata
  ) external override returns (bytes4) {
    // Only the factory can initialize, so an initialized msg.sender is a legit pool — no onlyPool needed.
    _requireInitialized(msg.sender);
    _afterSwapOracleStopLoss(msg.sender, packedSlot0Initial, packedSlot0Final, bidPriceX64, askPriceX64, zeroForOne);
    return IMetricOmmExtensions.afterSwap.selector;
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
