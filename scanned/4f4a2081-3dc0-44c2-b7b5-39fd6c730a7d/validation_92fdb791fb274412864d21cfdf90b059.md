### Title
Missing Minimum Timelock Validation in `OracleValueStopLossExtension.initialize()` Allows Zero-Delay Stop-Loss Parameter Changes — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension.initialize()` validates `drawdownE6` and `decayPerSecondE8` but applies **no minimum bound to the `timelock` parameter**. A pool initialized with `timelock = 0` allows the pool admin to propose and execute drawdown, decay, and watermark changes atomically in the same transaction, completely defeating the LP-protection guarantee the timelock is designed to enforce.

---

### Finding Description

During pool creation the factory calls `initialize` on each extension, passing `abi.encode(drawdownE6, decayPerSecondE8, timelockSeconds)` as `data`. The implementation validates the first two fields but silently accepts any value — including zero — for `timelock`:

```solidity
// OracleValueStopLossExtension.sol lines 56-62
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);   // enforces drawdownE6 <= 1e6
_validateDecay(decayPerSecondE8); // enforces decayPerSecondE8 <= 1e8
// ← NO _validateTimelock call; timelock = 0 is silently accepted
oracleStopLossConfig[pool] = PoolStopLossConfig({
    drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
});
``` [1](#0-0) 

When `timelock = 0`, the internal helper `_afterTimelock` computes `block.timestamp + 0 = block.timestamp`:

```solidity
// line 297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [2](#0-1) 

And `_requireElapsed` checks `block.timestamp < executeAfter`, which evaluates to `block.timestamp < block.timestamp` — always false — so the guard passes immediately:

```solidity
// line 301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
``` [3](#0-2) 

Every timelocked setter — `proposeOracleStopLossDrawdown` / `executeOracleStopLossDrawdown`, `proposeOracleStopLossDecay` / `executeOracleStopLossDecay`, and `proposeOracleStopLossHighWatermarks` / `executeOracleStopLossHighWatermarks` — routes through these two helpers, so all of them become instant when `timelock = 0`. [4](#0-3) 

The most destructive single-step: calling `proposeOracleStopLossDrawdown(pool, 0)` followed immediately by `executeOracleStopLossDrawdown(pool)` sets `drawdownE6 = 0`. The `afterSwap` hook then short-circuits at line 217 (`if (drawdown == 0) return;`), silently disabling all stop-loss checks for every subsequent swap. [5](#0-4) 

---

### Impact Explanation

The `OracleValueStopLossExtension` is the primary on-chain mechanism protecting LP principal from value drain. Its own NatSpec states: *"Drawdown and decay changes are timelocked so LPs can react."* With `timelock = 0` that guarantee is void. A pool admin can:

1. **Disable the stop-loss entirely** (`drawdownE6 → 0`) in one atomic transaction, then execute a large swap that drains LP value with no circuit-breaker.
2. **Maximize decay** (`decayPerSecondE8 → 1e8`, i.e. 100 %/s) instantly, collapsing all watermarks to zero within one second and removing every per-bin threshold.
3. **Zero out watermarks** for any bin instantly, resetting the high-water reference so the next swap sees no breach regardless of actual value loss.

All three paths result in direct loss of LP principal above Sherlock thresholds.

---

### Likelihood Explanation

Any pool creator can supply `timelockSeconds = 0` in `extensionInitData` — no special privilege beyond pool creation is required. The missing validation is a single omitted bounds check, analogous to the original isomorph bug where a developer wrote `3` (seconds) instead of `3 days`. A deployer who misreads the field as "number of blocks" or simply omits it produces the same zero-timelock outcome. Once deployed, the pool admin can exploit the condition at any time.

---

### Recommendation

Add a minimum timelock constant and enforce it in both `initialize` and `proposeOracleStopLossTimelock`:

```solidity
uint32 private constant MIN_TIMELOCK = 1 days; // e.g. 86400 seconds

function _validateTimelock(uint256 t) private pure {
    if (t < MIN_TIMELOCK) revert OracleStopLossTimelockTooShort(t);
}
```

Call `_validateTimelock(timelock)` alongside `_validateDrawdown` and `_validateDecay` in `initialize`, and again inside `proposeOracleStopLossTimelock` before storing `newTimelock`.

---

### Proof of Concept

```solidity
// Pool created with timelock = 0 in extensionInitData
bytes memory initData = abi.encode(
    uint32(50_000),  // drawdownE6 = 5%
    uint32(58),      // decayPerSecondE8
    uint32(0)        // timelock = 0  ← no validation, accepted silently
);

// Pool admin atomically disables stop-loss in one tx:
ext.proposeOracleStopLossDrawdown(pool, 0);   // executeAfter = block.timestamp
ext.executeOracleStopLossDrawdown(pool);       // passes: block.timestamp < block.timestamp == false

// oracleStopLossConfig[pool].drawdownE6 is now 0
// afterSwap returns immediately at "if (drawdown == 0) return;"
// All subsequent swaps bypass the stop-loss; LP value can be drained freely.
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L103-120)
```text
  function proposeOracleStopLossDrawdown(address pool_, uint256 newMaxDrawdownE6) external onlyPoolAdmin(pool_) {
    _validateDrawdown(newMaxDrawdownE6);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDrawdownE6 = uint32(newMaxDrawdownE6);
    sched.pendingDrawdownExecuteAfter = executeAfter;
    emit OracleStopLossDrawdownProposed(pool_, newMaxDrawdownE6, executeAfter);
  }

  function executeOracleStopLossDrawdown(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingDrawdownExecuteAfter == 0) revert OracleStopLossNoPendingDrawdown(pool_);
    _requireElapsed(sched.pendingDrawdownExecuteAfter);
    uint32 drawdown = sched.pendingDrawdownE6;
    oracleStopLossConfig[pool_].drawdownE6 = drawdown;
    (sched.pendingDrawdownE6, sched.pendingDrawdownExecuteAfter) = (0, 0);
    emit OracleStopLossDrawdownSet(pool_, drawdown);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L215-217)
```text
    PoolStopLossConfig memory cfg = oracleStopLossConfig[pool_];
    uint256 drawdown = cfg.drawdownE6;
    if (drawdown == 0) return;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L297-299)
```text
  function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L301-303)
```text
  function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(executeAfter, block.timestamp);
  }
```
