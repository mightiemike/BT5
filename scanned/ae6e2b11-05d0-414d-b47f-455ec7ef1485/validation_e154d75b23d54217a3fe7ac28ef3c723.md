### Title
Pool Admin Can Reduce Timelock to Zero and Atomically Disable All Stop-Loss Protections — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension` enforces a timelock on parameter changes (drawdown, decay, watermarks) to give LPs a reaction window before the pool admin can alter stop-loss settings. However, there is no minimum-timelock validation: the admin can propose `newTimelock = 0`, wait the current timelock to execute it, and then atomically propose and execute `drawdown = 0` in the same block — permanently disabling the stop-loss guard with zero LP reaction time for that final step.

---

### Finding Description

**Step 1 — Reduce timelock to zero (properly timelocked):**

`proposeOracleStopLossTimelock` calls `_afterTimelock`, which reads the *current* `oracleStopLossConfig[pool_].timelock` to compute `executeAfter`. [1](#0-0) 

There is no `_validateTimelock` call — `newTimelock = 0` is accepted without restriction. [2](#0-1) 

After the current timelock elapses, `executeOracleStopLossTimelock` writes `0` directly into `oracleStopLossConfig[pool_].timelock`: [3](#0-2) 

**Step 2 — Atomically propose + execute drawdown = 0:**

With `timelock = 0`, `_afterTimelock` now returns `block.timestamp + 0 = block.timestamp`. [1](#0-0) 

`_requireElapsed` checks `block.timestamp < executeAfter`. When `executeAfter == block.timestamp`, this is `false`, so the check passes immediately — propose and execute can occur in the same block (or same transaction via multicall): [4](#0-3) 

`_validateDrawdown(0)` also passes because `0 > E6` is false: [5](#0-4) 

**Step 3 — Stop-loss is permanently disabled:**

`_afterSwapOracleStopLoss` has an early-exit guard: `if (drawdown == 0) return;`. Once `drawdownE6 = 0` is written, every subsequent swap skips all watermark checks entirely: [6](#0-5) 

The same zero-timelock path applies to `proposeOracleStopLossDecay` and `proposeOracleStopLossHighWatermarks` — all three read `_afterTimelock` at proposal time: [7](#0-6) [8](#0-7) 

---

### Impact Explanation

The timelock is the sole mechanism protecting LPs from pool-admin-controlled stop-loss parameter changes. The NatSpec explicitly states: *"Drawdown and decay changes are timelocked so LPs can react."* Once the admin reduces the timelock to zero (which itself requires waiting the original timelock — so LPs have one warning window), all future parameter changes — including disabling the drawdown guard entirely — can be executed atomically with zero LP reaction time. This is a direct admin-boundary break: the pool admin exceeds the intended constraint by permanently eliminating the LP protection window for all subsequent proposals.

The concrete fund-loss path: after disabling the stop-loss, swaps that would have been reverted by `OracleStopLossTriggered` (value-draining trades against a manipulated or stale oracle mid) now execute freely, draining LP principal.

---

### Likelihood Explanation

Any pool admin can execute this two-phase attack. The only prerequisite is waiting the initial timelock once. The proposal is on-chain and visible, but LPs who do not monitor continuously (the NatSpec acknowledges this: *"monitor at least as often as the timelock"*) will miss the second phase entirely since it is atomic.

---

### Recommendation

1. **Enforce a minimum timelock floor.** Add a `_validateTimelock` function (e.g., `if (newTimelock < MIN_TIMELOCK) revert`) and call it in both `initialize` and `proposeOracleStopLossTimelock`. A reasonable floor is 1 hour or 24 hours.

2. **Use strict inequality in `_requireElapsed`.** Change `block.timestamp < executeAfter` to `block.timestamp <= executeAfter` so that a zero-delay proposal cannot be executed in the same block as it is proposed. This is a defense-in-depth measure even if a minimum timelock is added.

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_timelockZeroBypassesStopLoss() public {
    // Pool initialized with timelock = 1 days, drawdown = 500_000 (50%)
    address pool = createPoolWithStopLoss(1 days, 500_000, 58);

    // Phase 1: admin proposes timelock = 0, must wait 1 day
    vm.prank(poolAdmin);
    ext.proposeOracleStopLossTimelock(pool, 0);

    vm.warp(block.timestamp + 1 days + 1);

    vm.prank(poolAdmin);
    ext.executeOracleStopLossTimelock(pool);
    // oracleStopLossConfig[pool].timelock == 0 now

    // Phase 2: atomically propose + execute drawdown = 0 in same block
    vm.prank(poolAdmin);
    ext.proposeOracleStopLossDrawdown(pool, 0);
    // executeAfter == block.timestamp, _requireElapsed passes immediately

    vm.prank(poolAdmin);
    ext.executeOracleStopLossDrawdown(pool);
    // oracleStopLossConfig[pool].drawdownE6 == 0

    // Phase 3: swap that would have triggered stop-loss now succeeds
    assertEq(ext.oracleStopLossConfig(pool).drawdownE6, 0);
    // _afterSwapOracleStopLoss: `if (drawdown == 0) return;` — guard is dead
    performValueDrainingSwap(pool); // no revert
}
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L86-94)
```text
  function executeOracleStopLossTimelock(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingTimelockExecuteAfter == 0) revert OracleStopLossNoPendingTimelock(pool_);
    _requireElapsed(sched.pendingTimelockExecuteAfter);
    uint32 timelock = sched.pendingTimelock;
    oracleStopLossConfig[pool_].timelock = timelock;
    (sched.pendingTimelock, sched.pendingTimelockExecuteAfter) = (0, 0);
    emit OracleStopLossTimelockSet(pool_, timelock);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L133-135)
```text
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDecayPerSecondE8 = uint32(newDecayPerSecondE8);
    sched.pendingDecayExecuteAfter = executeAfter;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L162-162)
```text
    uint32 executeAfter = _afterTimelock(pool_);
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L305-307)
```text
  function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
  }
```
