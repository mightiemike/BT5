### Title
`_afterTimelock` uint32 Truncation Silently Bypasses the LP-Protection Timelock — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterTimelock()` computes `uint32(block.timestamp + oracleStopLossConfig[pool_].timelock)`. When the sum exceeds `type(uint32).max`, the result wraps to a value smaller than the current `block.timestamp`, causing `_requireElapsed` to pass immediately. A pool admin who sets `timelock` to any value ≥ ~2.54 billion seconds can silently disable the timelock that is supposed to protect LPs from sudden parameter changes.

---

### Finding Description

`_afterTimelock` is the sole function that computes the "execute-after" timestamp for every timelocked proposal (drawdown, decay, watermarks, and the timelock itself):

```solidity
// OracleValueStopLossExtension.sol L297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

`block.timestamp` is `uint256`. `timelock` is `uint32` (promoted to `uint256` in the addition). The sum is computed in full `uint256` precision, then **silently truncated** to `uint32`. No validation exists on the `timelock` value — unlike `drawdownE6` and `decayPerSecondE8`, which each have dedicated `_validateDrawdown` / `_validateDecay` guards.

At the current block timestamp (~1,753,000,000 in July 2026), any `timelock` value ≥ `4,294,967,296 − 1,753,000,000 ≈ 2,541,967,296` seconds (~80.6 years) causes the sum to exceed `uint32.max`. The truncated `executeAfter` wraps to a value **less than** `block.timestamp`, so `_requireElapsed` never reverts:

```solidity
// L301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
```

`type(uint32).max` (4,294,967,295 ≈ 136 years) is a plausible "maximum protection" value a pool admin might set. With `block.timestamp ≈ 1,753,000,000`:

```
executeAfter = uint32(1,753,000,000 + 4,294,967,295)
             = uint32(6,047,967,295)
             = 6,047,967,295 − 4,294,967,296
             = 1,752,999,999
```

Since `1,753,000,000 ≥ 1,752,999,999`, the elapsed check passes immediately — the timelock is completely bypassed.

---

### Impact Explanation

The timelock is the **only mechanism** protecting LPs from sudden pool-admin parameter changes. Once bypassed, the pool admin can:

1. Propose `drawdownE6 = 0` (disabling the stop-loss entirely) and execute it in the same block.
2. With the stop-loss disabled, execute a swap that drains LP value at a manipulated oracle price — a loss the stop-loss would have blocked.
3. Alternatively, propose and immediately execute watermark resets to zero, then drain through a subsequent swap.

This is a direct loss of LP principal with no time window for LPs to exit.

---

### Likelihood Explanation

The pool admin must set `timelock` to a value ≥ ~2.54 billion seconds. `type(uint32).max` is a natural "maximum" value an admin might choose. There is no on-chain validation preventing it. The initial timelock is set at factory initialization with no cap, and the timelock-change flow itself uses `_afterTimelock` with the **current** timelock — so if the initial timelock is 0, the admin can change it to an overflowing value in a single transaction.

---

### Recommendation

Add a `_validateTimelock` function analogous to `_validateDrawdown` and `_validateDecay`, capping `timelock` at a safe maximum (e.g., 365 days = 31,536,000 seconds, well within `uint32` range and far below the overflow threshold):

```solidity
uint32 private constant MAX_TIMELOCK = 365 days;

function _validateTimelock(uint32 timelock) private pure {
    if (timelock > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(timelock);
}
```

Call it in both `initialize` and `proposeOracleStopLossTimelock`. Alternatively, compute `executeAfter` in `uint256` and revert on overflow before truncating.

---

### Proof of Concept

```solidity
// Pool admin sets timelock = type(uint32).max at initialization (timelock = 0, so no delay)
vm.prank(address(factory));
extension.initialize(pool, abi.encode(uint32(0), uint32(0), uint32(0)));

vm.startPrank(poolAdmin);
// Step 1: set overflowing timelock (current timelock = 0, so executeAfter = uint32(ts + 0) = ts, passes immediately)
extension.proposeOracleStopLossTimelock(pool, type(uint32).max);
extension.executeOracleStopLossTimelock(pool);  // succeeds: executeAfter = ts, ts >= ts

// Step 2: propose drawdown = 0 (disabling stop-loss)
// _afterTimelock now returns uint32(ts + type(uint32).max) = ts - 1 < ts → passes immediately
extension.proposeOracleStopLossDrawdown(pool, 0);
extension.executeOracleStopLossDrawdown(pool);  // succeeds in same block — timelock bypassed

// Step 3: stop-loss is now disabled; LP-draining swap proceeds unchecked
vm.stopPrank();
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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
