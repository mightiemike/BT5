The overflow arithmetic is confirmed. Let me verify the exact comparison semantics in `_requireElapsed` to be precise about the uint32/uint256 comparison.

### Title
Unvalidated `timelock` in `initialize()` enables uint32 overflow to bypass all timelocked parameter changes — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension.initialize()` validates `drawdownE6` and `decayPerSecondE8` but applies **no upper-bound check on `timelock`**. A pool creator who passes `type(uint32).max` causes `_afterTimelock()` to silently wrap to a timestamp in the past, making every subsequent `_requireElapsed()` check pass immediately. The pool admin can then propose and execute any timelocked parameter change in the same block, defeating the LP-protection mechanism entirely.

---

### Finding Description

`initialize()` decodes three parameters and validates only two of them: [1](#0-0) 

```solidity
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);   // ✓ capped at 1e6
_validateDecay(decayPerSecondE8); // ✓ capped at 1e8
// timelock — no validation at all
oracleStopLossConfig[pool] = PoolStopLossConfig({
    drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
});
```

Every propose-path calls `_afterTimelock()`: [2](#0-1) 

```solidity
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}

function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
```

**Overflow arithmetic (July 2026, `block.timestamp ≈ 1_753_000_000`):**

| Step | Value |
|---|---|
| `block.timestamp + type(uint32).max` | `1_753_000_000 + 4_294_967_295 = 6_047_967_295` (uint256, no overflow) |
| `uint32(6_047_967_295)` | `6_047_967_295 mod 4_294_967_296 = 1_752_999_999` |
| `executeAfter` stored | `≈ block.timestamp − 1` |
| `_requireElapsed` check | `block.timestamp (1_753_000_000) < 1_752_999_999` → **false → no revert** |

The guard passes immediately on every call. All four propose+execute pairs (`drawdown`, `decay`, `timelock`, `highWatermarks`) are affected: [3](#0-2) [4](#0-3) 

---

### Impact Explanation

The timelock is the sole mechanism that gives LPs time to exit before the pool admin alters stop-loss parameters. With the timelock bypassed, the pool admin can in a single block:

1. **Set `drawdownE6 = 0`** — disables the stop-loss entirely; value can drain through swaps without triggering `OracleStopLossTriggered`.
2. **Set `decayPerSecondE8 = 1e8`** — collapses all watermarks to zero instantly on the next swap, removing any historical high-water protection.
3. **Raise watermarks to `type(uint104).max`** — makes every swap revert, locking LP funds in the pool.

All three outcomes represent direct LP fund loss or broken core pool functionality above contest thresholds.

---

### Likelihood Explanation

Pool creation via `createPool()` is permissionless — any address can deploy a pool with arbitrary `extensionInitData`. The factory passes the raw bytes directly to `initialize()` without inspecting the timelock field: [5](#0-4) 

A pool admin who wants to retain instant control over stop-loss parameters has a clear, low-cost path: encode `type(uint32).max` as the third word of `extensionInitData`. LPs observing `timelock = 4294967295` on-chain would likely interpret it as an extremely long delay (136 years), not as a bypassed guard.

---

### Recommendation

Add a maximum timelock validation in `initialize()`, mirroring the pattern already used for the other two parameters:

```solidity
uint32 private constant MAX_TIMELOCK = 365 days; // or a protocol-chosen cap

// in initialize():
if (timelock > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(timelock);
```

Alternatively, compute `executeAfter` in `uint256` and store it as `uint64` to eliminate the truncation entirely.

---

### Proof of Concept

```solidity
// Pool created with timelock = type(uint32).max
extension.initialize(pool, abi.encode(uint32(500_000), uint32(58), type(uint32).max));

// Same block: propose and execute drawdown change — no revert
vm.prank(admin);
extension.proposeOracleStopLossDrawdown(pool, 0);   // executeAfter = block.timestamp - 1
extension.executeOracleStopLossDrawdown(pool);       // _requireElapsed passes immediately

// Stop-loss is now disabled; swaps proceed regardless of value loss
assertEq(oracleStopLossConfig[pool].drawdownE6, 0);
```

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L103-110)
```text
  function proposeOracleStopLossDrawdown(address pool_, uint256 newMaxDrawdownE6) external onlyPoolAdmin(pool_) {
    _validateDrawdown(newMaxDrawdownE6);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDrawdownE6 = uint32(newMaxDrawdownE6);
    sched.pendingDrawdownExecuteAfter = executeAfter;
    emit OracleStopLossDrawdownProposed(pool_, newMaxDrawdownE6, executeAfter);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L157-165)
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
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L297-303)
```text
  function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
  }

  function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(executeAfter, block.timestamp);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L206-210)
```text
    for (uint256 i = 0; i < params.extensions.length; i++) {
      CallExtension.callExtension(
        params.extensions[i], abi.encodeCall(IMetricOmmExtensions.initialize, (pool, params.extensionInitData[i]))
      );
    }
```
