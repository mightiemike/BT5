Audit Report

## Title
Zero Timelock Accepted at Initialization and Post-Deployment Allows Pool Admin to Bypass LP-Protection Timelock and Disable Stop-Loss Guard — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

`OracleValueStopLossExtension.initialize` decodes a `timelock` parameter from caller-supplied `data` but applies no minimum-value validation, silently accepting `timelock = 0`. With a zero timelock, the pool admin can call `proposeOracleStopLossDrawdown` and `executeOracleStopLossDrawdown` in the same block, bypassing the LP reaction window the timelock is designed to enforce. Setting `drawdownE6 = 1e6` (100%) in this way sets `floorMultiplier = 0`, causing the stop-loss check to never trigger, permanently disabling the guard for all future swaps and exposing LP principal to unprotected drain.

## Finding Description

**Root cause — missing timelock validation in `initialize`:**

`initialize` validates `drawdownE6` and `decayPerSecondE8` but leaves `timelock` unchecked:

```solidity
// L56-62
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);    // ✓ checked
_validateDecay(decayPerSecondE8); // ✓ checked
// timelock — NOT checked; zero is silently accepted
oracleStopLossConfig[pool] = PoolStopLossConfig({
    drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
});
```

**Timelock bypass mechanics:**

`_afterTimelock` computes the execution deadline as `block.timestamp + timelock`. With `timelock = 0`, `executeAfter = block.timestamp`:

```solidity
// L297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

`_requireElapsed` reverts only if `block.timestamp < executeAfter`. With `executeAfter = block.timestamp`, the condition `block.timestamp < block.timestamp` is always `false`, so the check never reverts:

```solidity
// L301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(executeAfter, block.timestamp);
}
```

**Stop-loss disabled via `drawdownE6 = 1e6`:**

`_validateDrawdown` only rejects values strictly greater than `E6`, so `drawdownE6 = 1e6` passes:

```solidity
// L305-307
function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
}
```

In `_afterSwapOracleStopLoss`, `floorMultiplier = E6 - drawdown = 1e6 - 1e6 = 0`. Inside `_applyWatermark`:

```solidity
// L334
breached = metric < (hwm * floorMultiplier) / E6;
// → metric < 0  (uint256 comparison) → always false
```

`_checkAndUpdateWatermarks` never reverts, so `afterSwap` never blocks any swap direction regardless of value drained from LP bins.

**Second path — post-deployment timelock reduction:**

`proposeOracleStopLossTimelock` also applies no lower-bound check on `newTimelock`:

```solidity
// L78-84
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    emit OracleStopLossTimelockProposed(pool_, newTimelock, executeAfter);
}
```

A pool admin can propose `newTimelock = 0`, wait the current timelock period, execute it, and then immediately propose and execute any drawdown change in a single block.

## Impact Explanation

The `OracleValueStopLossExtension` is the primary on-chain mechanism preventing LP principal from being drained through adversarial or oracle-manipulated swaps. Its own NatSpec states the timelock exists so "LPs can react." With `timelock = 0`, that window is zero seconds. The pool admin can silently disable the guard in a single block, after which every subsequent swap executes without the stop-loss check. This is a direct admin-boundary break (pool admin bypasses the timelock constraint that is specifically designed to limit their power) resulting in direct loss of LP-deposited token0 and token1 principal with no on-chain recourse. This meets the "Admin-boundary break: pool admin exceeds caps, bypasses timelocks" allowed impact criterion.

## Likelihood Explanation

The factory calls `initialize` with admin-supplied `data`; no factory-level validation of the `timelock` field exists. A pool creator who is also the pool admin (a common pattern for permissioned pools) can deliberately pass `timelock = 0`. The same outcome is reachable post-deployment by reducing a non-zero timelock to zero via `proposeOracleStopLossTimelock(pool_, 0)` once the original delay elapses. Both paths require only pool-admin privilege, which is the semi-trusted role the timelock is specifically designed to constrain.

## Recommendation

Add a minimum-timelock guard in `initialize` and in `proposeOracleStopLossTimelock`:

```solidity
uint32 private constant MIN_TIMELOCK = 1 hours;

function _validateTimelock(uint32 timelock) private pure {
    if (timelock < MIN_TIMELOCK) revert OracleStopLossTimelockTooShort(timelock);
}
```

Apply `_validateTimelock(timelock)` alongside the existing `_validateDrawdown` / `_validateDecay` calls in `initialize` (L57-58), and apply it to `newTimelock` inside `proposeOracleStopLossTimelock` (L78). Additionally, consider rejecting `drawdownE6 == E6` in `_validateDrawdown` since a 100% drawdown is functionally equivalent to disabling the guard.

## Proof of Concept

1. Factory deploys a pool and calls `OracleValueStopLossExtension.initialize(pool, abi.encode(500_000, 58, 0))` — `drawdownE6 = 50%`, `decayPerSecondE8 = 58`, **`timelock = 0`**. No revert occurs because `timelock` is never validated.

2. LPs deposit into the pool, trusting the stop-loss guard is active.

3. Pool admin calls `proposeOracleStopLossDrawdown(pool, 1_000_000)` (100% drawdown). `_afterTimelock` returns `block.timestamp + 0 = block.timestamp`. `executeAfter = block.timestamp`.

4. In the same transaction, pool admin calls `executeOracleStopLossDrawdown(pool)`. `_requireElapsed(block.timestamp)` evaluates `block.timestamp < block.timestamp` → `false` → no revert. `drawdownE6` is set to `1_000_000`.

5. `floorMultiplier = 1e6 − 1e6 = 0`. All future `afterSwap` calls reach `_applyWatermark` with `floorMultiplier = 0`; `breached` is always `false`; the stop-loss never fires.

6. Swaps that drain LP bins below any prior watermark proceed without revert. LP principal is lost with no on-chain protection.