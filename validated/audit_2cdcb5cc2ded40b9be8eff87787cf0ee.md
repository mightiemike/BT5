Audit Report

## Title
`uint32` Truncation in `_afterTimelock` Allows Pool Admin to Bypass Stop-Loss Timelock Immediately — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

`_afterTimelock` computes `uint32(block.timestamp + oracleStopLossConfig[pool_].timelock)`. When `timelock` is set to `type(uint32).max`, the 256-bit addition overflows the 32-bit range and the truncated result is a timestamp already in the past, causing `_requireElapsed` to pass immediately. A pool admin can exploit this to bypass the timelock on all subsequent proposals and immediately execute stop-loss parameter changes that are supposed to give LPs advance notice.

## Finding Description

`_afterTimelock` at line 297–299 performs the deadline computation entirely in `uint256` and then silently truncates to `uint32`:

```solidity
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [1](#0-0) 

`_requireElapsed` at line 301–303 compares `block.timestamp` (uint256) against the stored `uint32 executeAfter`:

```solidity
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(executeAfter, block.timestamp);
}
``` [2](#0-1) 

`proposeOracleStopLossTimelock` accepts any `uint32 newTimelock` with no upper-bound cap:

```solidity
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    ...
}
``` [3](#0-2) 

With `block.timestamp ≈ 1,753,000,000` (July 2026) and `timelock = type(uint32).max = 4,294,967,295`:

```
1,753,000,000 + 4,294,967,295 = 6,047,967,295
6,047,967,295 % 2^32 = 1,752,999,999  ← already less than block.timestamp
```

`_requireElapsed(1,752,999,999)` passes immediately because `block.timestamp (1,753,000,000) >= 1,752,999,999`. Every subsequent `execute*` call on any proposal made while `timelock = uint32.max` is active bypasses the delay entirely.

Once the timelock is bypassed, the admin can immediately execute `drawdownE6 = E6`. In `_afterSwapOracleStopLoss`, this sets `floorMultiplier = E6 - E6 = 0`, making the breach condition `metric < (hwm * 0) / E6 = 0` permanently false — the stop-loss never triggers again: [4](#0-3) 

`_validateDrawdown` only rejects values strictly greater than `E6`, so `drawdownE6 = E6` is accepted: [5](#0-4) 

## Impact Explanation

This is a confirmed admin-boundary break: the pool admin exceeds the timelock constraint that is the sole mechanism bounding admin power over LP funds. With the stop-loss disabled (`floorMultiplier = 0`), the `afterSwap` hook never reverts, allowing swaps that drain LP token0/token1 balances from bins without triggering the guard. This is a direct loss of LP-deposited principal, qualifying as Critical/High under Sherlock thresholds.

## Likelihood Explanation

The attack requires a malicious pool admin — a semi-trusted role whose power is explicitly bounded by the timelock. The steps are fully deterministic: the admin waits out the initial timelock once (to set `timelock = uint32.max`), after which all future proposals execute instantly with zero delay. No special on-chain conditions, oracle manipulation, or external dependencies are required. The attack is repeatable and permanent once the timelock value is set.

## Recommendation

Cap `newTimelock` inside `proposeOracleStopLossTimelock` and perform deadline arithmetic in `uint256`:

```solidity
uint256 private constant MAX_TIMELOCK = 365 days;

function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    if (newTimelock > MAX_TIMELOCK) revert TimelockTooLarge(newTimelock);
    ...
}

function _afterTimelock(address pool_) private view returns (uint256) {
    return block.timestamp + oracleStopLossConfig[pool_].timelock;
}
```

Store and compare `executeAfter` as `uint256` throughout `PoolStopLossSchedule` and `_requireElapsed` to eliminate the truncation entirely.

## Proof of Concept

```
1. Pool initialized with timelock = 1 days.
2. Admin calls proposeOracleStopLossTimelock(pool, type(uint32).max).
   _afterTimelock returns uint32(block.timestamp + 1 days) — a future timestamp.
3. Admin waits 1 day, calls executeOracleStopLossTimelock.
   oracleStopLossConfig[pool].timelock = type(uint32).max.
4. Admin calls proposeOracleStopLossDrawdown(pool, E6).
   _afterTimelock: uint32(1_753_000_000 + 4_294_967_295) = uint32(6_047_967_295)
                 = 1_752_999_999  ← already in the past.
5. Admin immediately calls executeOracleStopLossDrawdown.
   _requireElapsed(1_752_999_999): 1_753_000_000 >= 1_752_999_999 → passes.
6. drawdownE6 = E6 → floorMultiplier = 0 → stop-loss permanently disabled.
7. Subsequent swaps drain LP bins without afterSwap hook reverting.
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L234-234)
```text
    uint256 floorMultiplier = E6 - drawdown;
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
