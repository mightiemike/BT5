### Title
Pool Admin Can Reduce Timelock to Zero and Atomically Disable All Stop-Loss Protections Without LP Reaction Window — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`proposeOracleStopLossTimelock` accepts `newTimelock = 0` with no minimum validation. Once executed, `_afterTimelock` returns `block.timestamp`, and `_requireElapsed(block.timestamp)` passes immediately (`block.timestamp < block.timestamp` is false). The pool admin can then propose and execute `drawdownE6 = 0` in the same block, which causes `_afterSwapOracleStopLoss` to return early unconditionally, disabling the entire stop-loss guard with zero LP reaction window.

---

### Finding Description

The timelock mechanism is designed to give LPs a reaction window before the pool admin can change stop-loss parameters. The NatDoc states explicitly: *"Drawdown and decay changes are timelocked so LPs can react."*

**Step 1 — Reduce timelock to zero (requires waiting current timelock T):**

`proposeOracleStopLossTimelock(pool_, 0)` is accepted without any minimum-timelock validation: [1](#0-0) 

After T seconds, `executeOracleStopLossTimelock` writes `oracleStopLossConfig[pool_].timelock = 0`: [2](#0-1) 

**Step 2 — Atomically propose + execute `drawdownE6 = 0`:**

With `timelock = 0`, `_afterTimelock` returns `block.timestamp`: [3](#0-2) 

`_requireElapsed(block.timestamp)` evaluates `block.timestamp < block.timestamp` → **false** → does not revert: [4](#0-3) 

So `proposeOracleStopLossDrawdown(pool_, 0)` followed immediately by `executeOracleStopLossDrawdown(pool_)` in the same block succeeds, writing `drawdownE6 = 0`.

**Step 3 — Stop-loss guard is fully disabled:**

`_afterSwapOracleStopLoss` short-circuits on `drawdown == 0`, skipping all watermark checks: [5](#0-4) 

The same zero-delay pattern applies to `proposeOracleStopLossDecay` / `executeOracleStopLossDecay` and `proposeOracleStopLossHighWatermarks` / `executeOracleStopLossHighWatermarks`, so all three parameter axes can be atomically corrupted.

---

### Impact Explanation

Once `drawdownE6 = 0`, the `afterSwap` hook returns early for every swap. The stop-loss that prevents per-share value from falling below the drawdown floor is completely inoperative. LPs are exposed to unlimited value leakage through swaps with no on-chain protection and no reaction window. This is a direct admin-boundary break: the pool admin exceeds the cap imposed by the timelock mechanism that was explicitly designed to constrain them.

**Severity: High** — broken core pool functionality (stop-loss guard) causing potential loss of LP principal with zero LP reaction window after the timelock is zeroed.

---

### Likelihood Explanation

The pool admin is a single EOA or contract address returned by `IMetricOmmPoolFactory(FACTORY).poolAdmin(pool_)`. [6](#0-5) 

The pool admin is explicitly *not* fully trusted — the timelock exists precisely to limit their power. The attack requires only two phases: wait the current timelock (visible on-chain, but LPs may not monitor), then atomically disable protections. No external oracle manipulation, no non-standard tokens, and no factory owner involvement is needed.

---

### Recommendation

1. **Enforce a minimum timelock floor** in `proposeOracleStopLossTimelock` (e.g., `require(newTimelock >= MIN_TIMELOCK)`), preventing reduction to zero.
2. **Use strict inequality** in `_requireElapsed`: change `block.timestamp < executeAfter` to `block.timestamp <= executeAfter` so that same-block execution is always rejected regardless of timelock value.
3. Optionally, **validate timelock at initialization** — `initialize` currently calls `_validateDrawdown` and `_validateDecay` but has no `_validateTimelock`: [7](#0-6) 

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_timelockZeroDisablesStopLoss() public {
    // Assume pool deployed with timelock = 1 days, drawdownE6 = 50_000 (5%)
    
    // Phase 1: propose timelock = 0 (must wait current timelock)
    vm.prank(poolAdmin);
    ext.proposeOracleStopLossTimelock(pool, 0);
    
    // Warp past current timelock
    vm.warp(block.timestamp + 1 days);
    
    // Execute timelock reduction — now oracleStopLossConfig[pool].timelock == 0
    vm.prank(poolAdmin);
    ext.executeOracleStopLossTimelock(pool);
    
    // Phase 2: same block — propose drawdown = 0
    vm.prank(poolAdmin);
    ext.proposeOracleStopLossDrawdown(pool, 0);
    // executeAfter = block.timestamp + 0 = block.timestamp
    
    // Same block — execute drawdown = 0 (block.timestamp < block.timestamp == false, passes)
    vm.prank(poolAdmin);
    ext.executeOracleStopLossDrawdown(pool);
    
    // Assert drawdown is now 0
    (uint32 drawdown,,,) = ext.oracleStopLossConfig(pool);
    assertEq(drawdown, 0);
    
    // Phase 3: swap that would have triggered stop-loss now succeeds
    // _afterSwapOracleStopLoss returns early at `if (drawdown == 0) return;`
    // No OracleStopLossTriggered revert — stop-loss is fully disabled
    _executeValueDrainingSwap(pool); // succeeds, no revert
}
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L31-35)
```text
  modifier onlyPoolAdmin(address pool_) {
    address poolAdmin = IMetricOmmPoolFactory(FACTORY).poolAdmin(pool_);
    if (msg.sender != poolAdmin) revert OnlyPoolAdmin(pool_, msg.sender, poolAdmin);
    _;
  }
```
