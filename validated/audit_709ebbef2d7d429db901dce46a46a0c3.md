Audit Report

## Title
Unbounded `timelock` in `_afterTimelock` silently wraps to a past timestamp, allowing pool admin to bypass LP-protection timelock — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

`OracleValueStopLossExtension` stores `timelock` as `uint32` and computes the execution deadline via `uint32(block.timestamp + timelock)`. No upper-bound is enforced on `timelock` at initialization or when updated. When `block.timestamp + timelock > type(uint32).max`, the cast silently wraps to a value smaller than the current timestamp, causing `_requireElapsed` to pass immediately and nullifying the LP-protection delay entirely.

## Finding Description

`_afterTimelock` computes the execution deadline:

```solidity
// L297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [1](#0-0) 

With `block.timestamp ≈ 1,753,000,000` and `type(uint32).max = 4,294,967,295`, any `timelock > 2,541,967,295` causes the addition to exceed `type(uint32).max`. The truncation wraps the result to a value smaller than the current timestamp (e.g., `uint32(1,753,000,000 + 4,294,967,295) = 1,752,999,999`).

`_requireElapsed` then compares the wrapped past value against `block.timestamp` (as uint256):

```solidity
// L301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
``` [2](#0-1) 

Since `block.timestamp` (uint256, ~1.753B) is always greater than the wrapped `executeAfter` (~1.752B), the check passes immediately — the timelock is silently nullified.

Neither `initialize` nor `proposeOracleStopLossTimelock` validates an upper bound on `timelock`. `initialize` calls `_validateDrawdown` and `_validateDecay` but stores `timelock` without any bound check: [3](#0-2) 

`proposeOracleStopLossTimelock` similarly stores `newTimelock` without validation: [4](#0-3) 

The existing validators `_validateDrawdown` and `_validateDecay` demonstrate the pattern is known and intentional for other parameters, but was omitted for `timelock`: [5](#0-4) 

## Impact Explanation

The timelock is the sole mechanism giving LPs time to react before the pool admin changes drawdown floor or decay rate. Once set to an overflow value, the pool admin can immediately propose and execute `drawdown = E6` (100%). This sets `floorMultiplier = E6 − E6 = 0`, making `_applyWatermark` evaluate `metric < (hwm * 0) / E6 = 0`, which is never true for any `uint256` metric: [6](#0-5) 

The stop-loss guard is permanently disabled, leaving LP principal unprotected against oracle-price manipulation or value leakage. This matches the allowed impact gate: **Admin-boundary break — pool admin bypasses timelocks** and **broken core pool functionality causing loss of LP assets**.

## Likelihood Explanation

The pool admin is semi-trusted. The NatDoc explicitly states the timelock exists so "LPs can react," implying LPs may not fully trust the pool admin. A pool admin who sets `timelock = type(uint32).max` (which reads as a "136-year timelock" to an LP) actually holds a zero-delay override. The attack requires only two admin calls after the current timelock elapses, or can be embedded at pool initialization with no prior timelock to wait through. The path is reachable, low-cost, and repeatable.

## Recommendation

Add an upper-bound check on `timelock` in both `initialize` and `proposeOracleStopLossTimelock`, analogous to the existing `_validateDrawdown` and `_validateDecay` guards:

```solidity
uint32 private constant MAX_TIMELOCK = 30 days; // 2_592_000 seconds

function _validateTimelock(uint32 timelock) private pure {
    if (timelock > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(timelock);
}
```

Apply `_validateTimelock` in `initialize` alongside `_validateDrawdown`/`_validateDecay`, and at the top of `proposeOracleStopLossTimelock` before storing `newTimelock`.

## Proof of Concept

```
block.timestamp ≈ 1_753_000_000 (mainnet, July 2025)
type(uint32).max = 4_294_967_295

Step 1 — Pool initialized with timelock = 7 days (604_800 s). No overflow.

Step 2 — Pool admin calls:
  proposeOracleStopLossTimelock(pool, type(uint32).max)
  // executeAfter = uint32(1_753_000_000 + 604_800) = valid future ts ✓

Step 3 — After 7 days, pool admin calls:
  executeOracleStopLossTimelock(pool)
  // oracleStopLossConfig[pool].timelock = 4_294_967_295

Step 4 — Pool admin calls:
  proposeOracleStopLossDrawdown(pool, 1_000_000)  // drawdown = E6 = 100%
  // _afterTimelock() = uint32(1_753_604_800 + 4_294_967_295)
  //                  = uint32(6_048_572_095)
  //                  = 1_753_604_799  ← PAST timestamp (wrapped)
  // sched.pendingDrawdownExecuteAfter = 1_753_604_799

Step 5 — Pool admin immediately calls (same block):
  executeOracleStopLossDrawdown(pool)
  // _requireElapsed(1_753_604_799):
  //   block.timestamp (1_753_604_800) < 1_753_604_799 → FALSE → no revert ✓
  // drawdown set to 1_000_000 (100%) — floorMultiplier = 0 — stop-loss disabled

LPs have zero blocks to react; the stop-loss guard is permanently open.

Alternative: initialize pool directly with timelock = type(uint32).max — bypass is
active from block 0, no waiting period required.
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
