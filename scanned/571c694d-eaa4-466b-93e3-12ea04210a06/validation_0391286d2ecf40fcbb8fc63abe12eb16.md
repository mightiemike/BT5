The key code paths are now fully traced. Let me analyze the exact mechanics.

**Step-by-step trace of the proposed attack:**

**Step 1 — Propose timelock reduction:**
`proposeOracleStopLossTimelock(pool, 0)` calls `_afterTimelock(pool_)`: [1](#0-0) 

This records `pendingTimelockExecuteAfter = block.timestamp + current_timelock` (e.g., `+86400`).

**Step 2 — Wait and execute:**
After 86400s, `executeOracleStopLossTimelock` passes `_requireElapsed`: [2](#0-1) 

`oracleStopLossConfig[pool_].timelock` is now set to `0`. [3](#0-2) 

**Step 3 — Immediately propose + execute drawdown=0:**
`proposeOracleStopLossDrawdown(pool, 0)` now computes `executeAfter = block.timestamp + 0 = block.timestamp`. [4](#0-3) 

`executeOracleStopLossDrawdown` checks `block.timestamp < block.timestamp` → **false** → does not revert. `drawdownE6` is set to `0` in the same block. [5](#0-4) 

**Step 4 — Stop-loss is silently disabled:**
In `_afterSwapOracleStopLoss`, the very first check is: [6](#0-5) 

`drawdown == 0` → `return`. The entire watermark/breach logic is skipped. All swaps pass unconditionally.

---

**Is this a real vulnerability?**

The attack path is mechanically valid and confirmed by the code. However, the critical question under the scope rules is: **is a malicious pool admin in scope?**

The scope explicitly lists as a valid impact:
> "Admin-boundary break: pool admin exceeds caps, **bypasses timelocks**, or factory/oracle role checks are bypassed by an unprivileged path."

The pool admin IS bypassing the timelock — the timelock's entire purpose is to give LPs a reaction window before the admin can change stop-loss parameters. By reducing the timelock to 0 (which itself only requires waiting the current timelock once), the admin permanently eliminates that window for all future changes. After that, drawdown, decay, and watermarks can all be changed atomically in a single block with zero LP notice.

This is not a "trusted factory owner" or "oracle admin" action — it is explicitly the pool admin bypassing the timelock protection that the extension was designed to enforce against the pool admin.

---

### Title
Pool Admin Can Reduce Timelock to Zero and Immediately Disable Stop-Loss Protection — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

### Summary
The `OracleValueStopLossExtension` uses a per-pool timelock to delay parameter changes (drawdown, decay, watermarks), giving LPs a reaction window. However, the timelock itself can be reduced to zero by the pool admin after waiting the current timelock once. Once the timelock is zero, all subsequent parameter changes — including setting `drawdownE6 = 0` — can be proposed and executed atomically in the same block, completely eliminating the LP protection window.

### Finding Description
`proposeOracleStopLossTimelock` records `pendingTimelockExecuteAfter = block.timestamp + oracleStopLossConfig[pool].timelock` (the **current** timelock). After waiting that duration, `executeOracleStopLossTimelock` sets the stored timelock to the new value (e.g., 0). From that point, `_afterTimelock` returns `block.timestamp + 0 = block.timestamp`. Any subsequent `propose*` call sets `executeAfter = block.timestamp`, and the corresponding `execute*` call checks `block.timestamp < block.timestamp` which is `false`, so it passes immediately. The pool admin can therefore call `proposeOracleStopLossDrawdown(pool, 0)` and `executeOracleStopLossDrawdown(pool)` in the same transaction, setting `drawdownE6 = 0`. In `_afterSwapOracleStopLoss`, the guard `if (drawdown == 0) return` causes the entire stop-loss check to be skipped for all future swaps.

### Impact Explanation
With stop-loss disabled, swaps that would have been blocked by the watermark/drawdown breach check proceed unconditionally. LPs are exposed to unlimited value extraction through adversarial swaps (e.g., at manipulated oracle prices) with no on-chain protection. The pool admin can also reset watermarks to zero immediately after disabling the timelock, removing any historical high-water reference. This constitutes a direct admin-boundary break: the timelock was the only mechanism preventing the pool admin from making instant, LP-hostile parameter changes.

### Likelihood Explanation
Any pool admin who is willing to wait the initial timelock period (e.g., 1 day) can execute this attack. The steps are fully deterministic and require no external conditions, oracle manipulation, or special token behavior. The attack is permanent — once the timelock is 0, it cannot be restored by LPs.

### Recommendation
The timelock reduction should be subject to a **minimum floor** that cannot be reduced below a protocol-set constant (e.g., `MIN_TIMELOCK`). Alternatively, timelock reductions should require waiting the **new** timelock (not the current one) — or better, the **maximum** of the current and new timelock — so that reducing the timelock to 0 requires waiting 0 seconds under the new value, which is trivially bypassable. The correct fix is to enforce a non-zero minimum timelock at the protocol level, or to require that any timelock reduction waits `max(current, new)` before taking effect.

### Proof of Concept
```solidity
// 1. Initialize pool with timelock=86400, drawdown=500_000 (50%)
extension.initialize(pool, abi.encode(uint32(500_000), uint32(0), uint32(86400)));

// 2. Pool admin proposes timelock=0 (executeAfter = now + 86400)
vm.prank(admin);
extension.proposeOracleStopLossTimelock(pool, 0);

// 3. Wait the current timelock
vm.warp(block.timestamp + 86400);

// 4. Execute — timelock is now 0
vm.prank(admin);
extension.executeOracleStopLossTimelock(pool);
assert(oracleStopLossConfig[pool].timelock == 0);

// 5. In the same block: propose + execute drawdown=0
vm.startPrank(admin);
extension.proposeOracleStopLossDrawdown(pool, 0);   // executeAfter = block.timestamp
extension.executeOracleStopLossDrawdown(pool);       // block.timestamp < block.timestamp == false → passes
vm.stopPrank();
assert(oracleStopLossConfig[pool].drawdownE6 == 0);

// 6. Stop-loss is now disabled: afterSwap returns immediately for all swaps
// Any swap that would have triggered OracleStopLossTriggered now passes silently
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L90-93)
```text
    uint32 timelock = sched.pendingTimelock;
    oracleStopLossConfig[pool_].timelock = timelock;
    (sched.pendingTimelock, sched.pendingTimelockExecuteAfter) = (0, 0);
    emit OracleStopLossTimelockSet(pool_, timelock);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L103-109)
```text
  function proposeOracleStopLossDrawdown(address pool_, uint256 newMaxDrawdownE6) external onlyPoolAdmin(pool_) {
    _validateDrawdown(newMaxDrawdownE6);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDrawdownE6 = uint32(newMaxDrawdownE6);
    sched.pendingDrawdownExecuteAfter = executeAfter;
    emit OracleStopLossDrawdownProposed(pool_, newMaxDrawdownE6, executeAfter);
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
