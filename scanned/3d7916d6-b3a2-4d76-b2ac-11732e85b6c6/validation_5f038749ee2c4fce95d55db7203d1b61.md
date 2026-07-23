### Title
Pool admin can reduce `OracleValueStopLossExtension` timelock to zero, enabling atomic bypass of all timelocked LP-protection parameter changes — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension` enforces a per-pool timelock on drawdown, decay, and watermark changes so LPs can react before their protection parameters are altered. However, `proposeOracleStopLossTimelock` accepts any `uint32` value including `0` with no minimum bound enforced. Once the timelock is reduced to `0`, the admin can propose and immediately execute any subsequent parameter change in the same block — including setting `drawdownE6 = 0`, which completely disables the stop-loss guard. LPs who deposited trusting the guard have their principal exposed to value-leaking swaps with no on-chain recourse.

---

### Finding Description

The `OracleValueStopLossExtension` is designed to protect LP principal by blocking swaps that push per-bin value below a configured drawdown floor. The NatSpec explicitly states the timelock exists so LPs can react:

> *"Drawdown and decay changes are timelocked so LPs can react; monitor at least as often as the timelock or trust the pool admin."*

The timelock is stored in `oracleStopLossConfig[pool].timelock` and used by `_afterTimelock`:

```solidity
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [1](#0-0) 

`proposeOracleStopLossTimelock` accepts any `uint32` value with no minimum check:

```solidity
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    emit OracleStopLossTimelockProposed(pool_, newTimelock, executeAfter);
}
``` [2](#0-1) 

Compare with `_validateDrawdown` and `_validateDecay`, which only enforce upper bounds — there is no `_validateTimelock` call anywhere:

```solidity
function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
}
function _validateDecay(uint256 decayPerSecondE8) private pure {
    if (decayPerSecondE8 > E8) revert OracleStopLossDecayTooLarge(decayPerSecondE8);
}
``` [3](#0-2) 

The same absence of a minimum bound applies in `initialize`, where `timelock` is decoded and stored without validation:

```solidity
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);
_validateDecay(decayPerSecondE8);
// no _validateTimelock
oracleStopLossConfig[pool] = PoolStopLossConfig({
    drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
});
``` [4](#0-3) 

Once `timelock = 0` is committed, `_afterTimelock` returns `block.timestamp`, and `_requireElapsed` passes immediately:

```solidity
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(executeAfter, block.timestamp);
}
``` [5](#0-4) 

The admin can then set `drawdownE6 = 0` in the same block. The guard's entry point short-circuits on zero drawdown:

```solidity
uint256 drawdown = cfg.drawdownE6;
if (drawdown == 0) return;
``` [6](#0-5) 

The stop-loss is now completely disabled. All subsequent swaps proceed without any per-bin value check, regardless of how much LP principal is drained.

---

### Impact Explanation

LPs who deposited into a pool relying on `OracleValueStopLossExtension` as a principal-protection guard lose that protection entirely. Swaps that would have triggered `OracleStopLossTriggered` and reverted now succeed, allowing value to leak from LP bins without bound. This is a direct loss of LP principal — the exact impact class the stop-loss was designed to prevent.

The `afterSwap` hook is the only on-chain mechanism blocking value-leaking swaps on these pools. Once `drawdownE6 = 0`, the hook returns `IMetricOmmExtensions.afterSwap.selector` unconditionally, and the pool settles every swap regardless of per-share value degradation. [7](#0-6) 

---

### Likelihood Explanation

The attack requires the pool admin to act maliciously. The pool admin is a semi-trusted role — the protocol documentation explicitly states LPs must "monitor at least as often as the timelock or trust the pool admin." The timelock is the on-chain substitute for that trust. Reducing it to zero removes the only on-chain LP protection against a rogue admin. The two-step sequence (reduce timelock → disable drawdown) requires waiting out the current timelock period, but after that the attack is atomic and undetectable until the next swap.

**Low likelihood × High impact = Medium severity** (matching the external report's classification for the analogous NounsDAO finding).

---

### Recommendation

1. Enforce a `MIN_TIMELOCK` (e.g., `1 days`) in both `initialize` and `proposeOracleStopLossTimelock`:
   ```solidity
   uint32 private constant MIN_TIMELOCK = 1 days;

   function _validateTimelock(uint32 timelock) private pure {
       if (timelock < MIN_TIMELOCK) revert OracleStopLossTimelockTooShort(timelock);
   }
   ```
2. Apply `_validateTimelock` in `initialize` alongside the existing `_validateDrawdown` / `_validateDecay` calls.
3. Apply `_validateTimelock` in `proposeOracleStopLossTimelock` before recording the pending value.

---

### Proof of Concept

```solidity
// Pool deployed with timelock = 3 days, drawdown = 50_000 (5%)
// LPs deposit trusting the stop-loss guard.

// Step 1: Admin proposes timelock = 0 (no minimum check reverts this)
extension.proposeOracleStopLossTimelock(pool, 0);
// pendingTimelockExecuteAfter = block.timestamp + 3 days

// Step 2: Wait 3 days (current timelock elapses)
vm.warp(block.timestamp + 3 days);
extension.executeOracleStopLossTimelock(pool);
// oracleStopLossConfig[pool].timelock = 0

// Step 3: Immediately propose drawdown = 0
// _afterTimelock returns block.timestamp + 0 = block.timestamp
extension.proposeOracleStopLossDrawdown(pool, 0);
// pendingDrawdownExecuteAfter = block.timestamp

// Step 4: Immediately execute (block.timestamp < block.timestamp is false → passes)
extension.executeOracleStopLossDrawdown(pool);
// oracleStopLossConfig[pool].drawdownE6 = 0

// Step 5: Any swap now bypasses the stop-loss entirely
// _afterSwapOracleStopLoss: drawdown == 0 → return (no check)
pool.swap(recipient, zeroForOne, amount, priceLimit, callbackData, extensionData);
// Succeeds even if LP value per share has dropped 50% — guard is silent.
``` [8](#0-7) [9](#0-8) [10](#0-9)

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L78-93)
```text
  function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    emit OracleStopLossTimelockProposed(pool_, newTimelock, executeAfter);
  }

  function executeOracleStopLossTimelock(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingTimelockExecuteAfter == 0) revert OracleStopLossNoPendingTimelock(pool_);
    _requireElapsed(sched.pendingTimelockExecuteAfter);
    uint32 timelock = sched.pendingTimelock;
    oracleStopLossConfig[pool_].timelock = timelock;
    (sched.pendingTimelock, sched.pendingTimelockExecuteAfter) = (0, 0);
    emit OracleStopLossTimelockSet(pool_, timelock);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L103-119)
```text
  function proposeOracleStopLossDrawdown(address pool_, uint256 newMaxDrawdownE6) external onlyPoolAdmin(pool_) {
    _validateDrawdown(newMaxDrawdownE6);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDrawdownE6 = uint32(newMaxDrawdownE6);
    sched.pendingDrawdownExecuteAfter = executeAfter;
    emit OracleStopLossDrawdownProposed(pool_, newMaxDrawdownE6, executeAfter);
  }

  function executeOracleStopLossDrawdown(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingDrawdownExecuteAfter == 0) revert OracleStopLossNoPendingDrawdown(pool_);
    _requireElapsed(sched.pendingDrawdownExecuteAfter);
    uint32 drawdown = sched.pendingDrawdownE6;
    oracleStopLossConfig[pool_].drawdownE6 = drawdown;
    (sched.pendingDrawdownE6, sched.pendingDrawdownExecuteAfter) = (0, 0);
    emit OracleStopLossDrawdownSet(pool_, drawdown);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L185-204)
```text
  function afterSwap(
    address,
    address,
    bool zeroForOne,
    int128,
    uint128,
    uint256 packedSlot0Initial,
    uint256 packedSlot0Final,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    int128,
    int128,
    uint256,
    bytes calldata
  ) external override returns (bytes4) {
    // Only the factory can initialize, so an initialized msg.sender is a legit pool — no onlyPool needed.
    _requireInitialized(msg.sender);
    _afterSwapOracleStopLoss(msg.sender, packedSlot0Initial, packedSlot0Final, bidPriceX64, askPriceX64, zeroForOne);
    return IMetricOmmExtensions.afterSwap.selector;
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L305-311)
```text
  function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
  }

  function _validateDecay(uint256 decayPerSecondE8) private pure {
    if (decayPerSecondE8 > E8) revert OracleStopLossDecayTooLarge(decayPerSecondE8);
  }
```
