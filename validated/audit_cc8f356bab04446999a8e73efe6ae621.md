### Title
Unbounded `newTimelock` in `proposeOracleStopLossTimelock` Causes uint32 Wrap, Bypassing the Stop-Loss Timelock Guard - (File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol)

---

### Summary

`proposeOracleStopLossTimelock` accepts any `uint32 newTimelock` with no upper-bound validation. Setting it to `type(uint32).max` causes `_afterTimelock` to silently truncate the uint256 sum back to a past timestamp, making every subsequent proposal immediately executable. The pool admin can then instantly set `drawdownE6 = E6` (100 %), collapsing `floorMultiplier` to zero and permanently disabling the stop-loss guard for all LP positions.

---

### Finding Description

`drawdownE6` and `decayPerSecondE8` are both validated on proposal: [1](#0-0) [2](#0-1) 

The timelock proposal has no equivalent guard: [3](#0-2) 

`_afterTimelock` adds the stored `uint32` timelock to `block.timestamp` in `uint256`, then **truncates to `uint32`**: [4](#0-3) 

With `timelock = type(uint32).max` (4 294 967 295) and `block.timestamp ≈ 1 753 000 000` (July 2026):

```
uint32(1_753_000_000 + 4_294_967_295)
= uint32(6_047_967_295)
= 6_047_967_295 % 4_294_967_296
= 1_752_999_999          ← one second in the past
```

`_requireElapsed` then passes immediately for every subsequent proposal: [5](#0-4) 

The same absence of validation exists in `initialize`, where `timelock` is decoded and stored without any check: [6](#0-5) 

Compare: `_validateDrawdown` and `_validateDecay` both enforce hard ceilings, but no `_validateTimelock` exists: [7](#0-6) 

---

### Impact Explanation

Once the timelock is set to `type(uint32).max`, the admin can atomically propose and execute `drawdownE6 = E6` (the maximum value that passes `_validateDrawdown`). This sets:

```
floorMultiplier = E6 - drawdown = 1e6 - 1e6 = 0
```

Inside `_applyWatermark`, the breach condition becomes:

```solidity
breached = metric < (hwm * 0) / E6;   // always false
``` [8](#0-7) 

The stop-loss guard is permanently silenced. Any subsequent oracle-price manipulation or value extraction through swaps will not be blocked by `afterSwap`, exposing all LP principal in every bin the extension monitors. [9](#0-8) 

---

### Likelihood Explanation

The trigger requires the pool admin — a semi-trusted role whose power is explicitly bounded by the timelock mechanism. The timelock is the only on-chain constraint preventing the admin from making instant, LP-adverse parameter changes. Bypassing it is an admin-boundary break explicitly listed in the allowed impact gate. The initial timelock can be zero (as shown in tests), making the entire attack executable in a single block with no prior waiting period. [10](#0-9) 

---

### Recommendation

Add a `_validateTimelock` function mirroring the existing validators and call it in both `initialize` and `proposeOracleStopLossTimelock`:

```solidity
uint32 private constant MAX_TIMELOCK = 365 days; // example ceiling

function _validateTimelock(uint256 timelock) private pure {
    if (timelock > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(timelock);
}
```

Apply it in `initialize`:

```solidity
_validateDrawdown(drawdownE6);
_validateDecay(decayPerSecondE8);
_validateTimelock(timelock);   // add this
```

And in `proposeOracleStopLossTimelock`:

```solidity
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    _validateTimelock(newTimelock);   // add this
    ...
}
```

---

### Proof of Concept

```solidity
// Pool initialized with timelock = 0 (no initial delay)
extension.initialize(pool, abi.encode(uint32(50_000), uint32(58), uint32(0)));

// Step 1: Admin sets timelock to type(uint32).max — no validation, passes silently
vm.prank(admin);
extension.proposeOracleStopLossTimelock(pool, type(uint32).max);
// executeAfter = uint32(block.timestamp + 0) = block.timestamp → passes immediately
extension.executeOracleStopLossTimelock(pool);
// oracleStopLossConfig[pool].timelock == type(uint32).max

// Step 2: Admin proposes drawdown = E6 (100%)
// _afterTimelock returns uint32(block.timestamp + type(uint32).max) = past timestamp
vm.prank(admin);
extension.proposeOracleStopLossDrawdown(pool, 1e6);
// executeAfter is in the past → _requireElapsed passes immediately
extension.executeOracleStopLossDrawdown(pool);
// drawdownE6 == 1e6 → floorMultiplier == 0 → stop-loss permanently disabled

// Step 3: Verify stop-loss no longer triggers even on 100% value loss
// (afterSwap will not revert regardless of bin value drop)
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L56-67)
```text
    (uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
    _validateDrawdown(drawdownE6);
    _validateDecay(decayPerSecondE8);

    oracleStopLossConfig[pool] = PoolStopLossConfig({
      drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
    });

    emit OracleStopLossDrawdownSet(pool, drawdownE6);
    emit OracleStopLossDecaySet(pool, decayPerSecondE8);
    emit OracleStopLossTimelockSet(pool, timelock);
    return IMetricOmmExtensions.initialize.selector;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L78-84)
```text
  function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    emit OracleStopLossTimelockProposed(pool_, newTimelock, executeAfter);
  }
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L130-137)
```text
  function proposeOracleStopLossDecay(address pool_, uint256 newDecayPerSecondE8) external onlyPoolAdmin(pool_) {
    _validateDecay(newDecayPerSecondE8);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDecayPerSecondE8 = uint32(newDecayPerSecondE8);
    sched.pendingDecayExecuteAfter = executeAfter;
    emit OracleStopLossDecayProposed(pool_, newDecayPerSecondE8, executeAfter);
  }
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L305-311)
```text
  function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
  }

  function _validateDecay(uint256 decayPerSecondE8) private pure {
    if (decayPerSecondE8 > E8) revert OracleStopLossDecayTooLarge(decayPerSecondE8);
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

**File:** metric-periphery/test/extensions/OracleValueStopLossSubExtension.t.sol (L54-57)
```text
  function _initPool(address pool, uint32 drawdownE6, uint32 decayE8, uint32 timelock) internal {
    vm.prank(address(factoryStub));
    extension.initialize(pool, abi.encode(drawdownE6, decayE8, timelock));
  }
```
