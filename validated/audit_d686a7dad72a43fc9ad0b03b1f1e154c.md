Audit Report

## Title
`OracleValueStopLossExtension._afterTimelock` uint32 Overflow Lets Pool Admin Silently Bypass LP Protection Timelock — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

`_afterTimelock` truncates `block.timestamp + timelock` from `uint256` to `uint32` without overflow protection. A pool admin who sets `timelock = type(uint32).max` causes the addition to wrap to a past timestamp, making every subsequent propose-then-execute cycle immediately executable in the same block. The LP protection window guaranteed by the NatDoc is silently eliminated.

## Finding Description

`_afterTimelock` computes the execution deadline for all timelocked parameter changes:

```solidity
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [1](#0-0) 

`block.timestamp` is `uint256`; the addition is performed in `uint256` space and then truncated to `uint32`. With `timelock = type(uint32).max = 4,294,967,295` and `block.timestamp ≈ 1,753,000,000` (July 2026):

```
1,753,000,000 + 4,294,967,295 = 6,047,967,295
uint32(6,047,967,295) = 6,047,967,295 mod 2^32 = 1,752,999,999
```

`1,752,999,999 < block.timestamp`, so `_requireElapsed` (`block.timestamp < executeAfter`) is immediately `false` — the check passes in the **same block** as the proposal. [2](#0-1) 

Neither `initialize` nor `proposeOracleStopLossTimelock` validates the timelock value. In `initialize`, `drawdownE6` and `decayPerSecondE8` are validated, but `timelock` is stored as-is with no cap: [3](#0-2) 

`proposeOracleStopLossTimelock` accepts any `uint32` value without a cap: [4](#0-3) 

**Attack path:**
1. Pool is deployed with a legitimate timelock (e.g., 1 day).
2. Pool admin calls `proposeOracleStopLossTimelock(pool, type(uint32).max)`. The proposal's own `executeAfter = block.timestamp + 1 day` (uses the current 1-day timelock — visible to LPs).
3. After 1 day, admin calls `executeOracleStopLossTimelock` — `timelock` is now `type(uint32).max`.
4. Admin calls `proposeOracleStopLossDrawdown(pool, 0)`. `_afterTimelock` overflows → `executeAfter` is a past timestamp. [5](#0-4) 
5. Admin immediately calls `executeOracleStopLossDrawdown` in the **same block** — drawdown set to 0, stop-loss disabled. [6](#0-5) 
6. LPs had **zero** reaction window for step 5.

The same overflow applies to `proposeOracleStopLossDecay` and `proposeOracleStopLossHighWatermarks`, which also call `_afterTimelock`. [7](#0-6) [8](#0-7) 

## Impact Explanation

The `OracleValueStopLossExtension` NatDoc explicitly states: *"Drawdown and decay changes are timelocked so LPs can react."* [9](#0-8) 

The `afterSwap` hook enforces the stop-loss on every swap. Setting `drawdownE6 = 0` disables the guard entirely (`if (drawdown == 0) return;`), removing the mechanism that blocks value-extracting swaps. [10](#0-9) 

This is a direct admin-boundary break: the pool admin exceeds the timelock constraint that is the explicit cap on their power over LP funds, enabling removal of LP principal protection without any LP reaction window.

## Likelihood Explanation

The pool admin is semi-trusted only inside caps and timelocks. The overflow requires a deliberate action (`type(uint32).max`), but the code provides no guard. Any pool admin who understands the truncation can exploit it after waiting the initial timelock period. The `OracleStopLossTimelockProposed` event emitted in step 2 shows `type(uint32).max` as the proposed value — LPs monitoring events would interpret this as a 136-year timelock and consider themselves safe, not realizing the overflow effect on all subsequent proposals.

## Recommendation

Add a maximum timelock cap in both `initialize` and `proposeOracleStopLossTimelock`:

```solidity
uint32 private constant MAX_TIMELOCK = 365 days; // 31_536_000 — fits in uint32, no overflow risk

// In initialize:
if (timelock > MAX_TIMELOCK) revert InvalidTimelock(timelock);

// In proposeOracleStopLossTimelock:
if (newTimelock > MAX_TIMELOCK) revert InvalidTimelock(newTimelock);
```

Alternatively, perform the addition in `uint256` in `_afterTimelock` and revert if the result exceeds `type(uint32).max` before casting.

## Proof of Concept

```solidity
function test_timelockOverflowBypassesProtection() public {
    // Pool deployed with 1-day timelock (legitimate setup)
    // Admin proposes type(uint32).max as new timelock
    vm.prank(admin);
    extension.proposeOracleStopLossTimelock(address(pool), type(uint32).max);

    // Wait 1 day — the proposal is now executable under the current 1-day timelock
    vm.warp(block.timestamp + 1 days);
    vm.prank(admin);
    extension.executeOracleStopLossTimelock(address(pool));
    // timelock is now type(uint32).max

    // Propose drawdown = 0 — _afterTimelock overflows to a past timestamp
    vm.prank(admin);
    extension.proposeOracleStopLossDrawdown(address(pool), 0);

    // Execute immediately in the SAME block — no LP protection window
    vm.prank(admin);
    extension.executeOracleStopLossDrawdown(address(pool)); // succeeds without waiting

    // Stop-loss is now disabled — LPs had zero reaction time
    (uint32 drawdown,,,) = extension.oracleStopLossConfig(address(pool));
    assertEq(drawdown, 0);
}
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L13-16)
```text
/// @title OracleValueStopLossExtension
/// @notice Tracks per-bin value per share in token0 and token1 terms at the oracle mid,
///         against decaying high watermarks. Drawdown and decay changes are timelocked so LPs
///         can react; monitor at least as often as the timelock or trust the pool admin.
```

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L112-120)
```text
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L157-166)
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
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L216-217)
```text
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
