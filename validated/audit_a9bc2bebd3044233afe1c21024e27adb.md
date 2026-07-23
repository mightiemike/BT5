### Title
Unchecked `newTimelock` in `proposeOracleStopLossTimelock` Enables uint32 Overflow, Bypassing LP-Protection Timelock — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`proposeOracleStopLossTimelock` accepts any `uint32` value for `newTimelock` with no upper-bound validation. Setting `newTimelock = type(uint32).max` causes `_afterTimelock` to silently truncate a uint256 sum into a past uint32 timestamp, making every subsequent proposal (drawdown, decay, timelock) immediately executable. The pool admin can then atomically disable the stop-loss guard and expose LP principal to unprotected value loss.

---

### Finding Description

`proposeOracleStopLossTimelock` stores the caller-supplied value directly with no cap: [1](#0-0) 

`_afterTimelock` computes the execution deadline by adding the stored `uint32` timelock to `block.timestamp` (uint256) and then **explicitly casting** the result back to `uint32`: [2](#0-1) 

Solidity 0.8.x explicit casts do **not** revert on truncation. With `block.timestamp ≈ 1,750,000,000` and `timelock = type(uint32).max = 4,294,967,295`:

```
sum  = 1,750,000,000 + 4,294,967,295 = 6,044,967,295   (uint256, no overflow)
uint32(6,044,967,295) = 6,044,967,295 mod 4,294,967,296 = 1,749,999,999
```

`executeAfter = 1,749,999,999` is **one second in the past**. `_requireElapsed` passes immediately: [3](#0-2) 

Once the overflowed timelock is live, every call to `proposeOracleStopLossDrawdown` / `proposeOracleStopLossDecay` / `proposeOracleStopLossTimelock` produces an `executeAfter` in the past, so the matching `execute*` call succeeds in the same block.

The pool admin can then set `drawdownE6 = E6 = 1,000,000` (the maximum allowed by `_validateDrawdown`): [4](#0-3) 

With `drawdownE6 = E6`, `floorMultiplier = E6 − drawdownE6 = 0`. Inside `_applyWatermark`: [5](#0-4) 

`breached = metric < (hwm * 0) / E6 = 0` is always `false` for any `uint256 metric`. The stop-loss guard is permanently silenced.

---

### Impact Explanation

LPs who accepted a pool specifically because it carried a meaningful timelock (e.g., 7 days) to protect against sudden stop-loss weakening lose that protection entirely. After the guard is disabled, the pool admin can execute swaps that drain LP-owned token balances without the `afterSwap` hook ever reverting. This is a direct loss of LP principal above Sherlock thresholds and an explicit admin-boundary break (pool admin bypasses a timelock that is the sole LP safety mechanism for this extension).

---

### Likelihood Explanation

The pool admin is a semi-trusted role that is explicitly permitted to call `proposeOracleStopLossTimelock`. The only prerequisite is waiting for the **current** timelock to elapse once (to execute the `type(uint32).max` proposal). After that single wait, all future parameter changes are immediately executable with no further delay. Any pool admin who turns adversarial after deployment can execute this path.

---

### Recommendation

Add an upper-bound check in `proposeOracleStopLossTimelock` (and mirror it in `initialize`) to prevent values that would overflow `_afterTimelock`:

```solidity
uint32 constant MAX_TIMELOCK = 365 days; // or another protocol-chosen ceiling

function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock)
    external onlyPoolAdmin(pool_)
{
    if (newTimelock > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(newTimelock);
    // ... existing logic
}
```

Apply the same guard inside `initialize` for the constructor-time `timelock` parameter.

---

### Proof of Concept

```solidity
// Assume pool was initialized with timelock = 1 days, drawdown = 50_000.
// Admin waits 1 day, then:

// Step 1: set timelock to type(uint32).max
extension.proposeOracleStopLossTimelock(pool, type(uint32).max);
vm.warp(block.timestamp + 1 days);          // wait current timelock
extension.executeOracleStopLossTimelock(pool);

// Step 2: now _afterTimelock overflows → executeAfter is in the past
// Propose and execute drawdown = 1e6 in the SAME block
extension.proposeOracleStopLossDrawdown(pool, 1e6);
extension.executeOracleStopLossDrawdown(pool); // succeeds immediately

// Step 3: verify stop-loss is dead
(uint32 dd,,,) = extension.oracleStopLossConfig(pool);
assertEq(dd, 1e6); // floorMultiplier = 0 → breach never fires
```

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L305-307)
```text
  function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
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
