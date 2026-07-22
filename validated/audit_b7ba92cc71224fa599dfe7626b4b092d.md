### Title
`uint32` Overflow in `_afterTimelock` Allows Pool Admin to Bypass Stop-Loss Timelock Immediately — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterTimelock()` computes the unlock timestamp as `uint32(block.timestamp + oracleStopLossConfig[pool_].timelock)`. Because `block.timestamp` is `uint256` and `timelock` is `uint32`, the addition is performed in 256-bit space and then silently truncated to 32 bits. When `timelock` is set to a value large enough that `block.timestamp + timelock > type(uint32).max`, the truncated result wraps to a value smaller than `block.timestamp`, making `_requireElapsed` pass immediately. A pool admin can exploit this to bypass the timelock that is supposed to give LPs time to react before stop-loss parameters are changed.

---

### Finding Description

`_afterTimelock` is the single function that computes the `executeAfter` deadline for every timelocked proposal (drawdown, decay, high-watermarks, and the timelock itself): [1](#0-0) 

```solidity
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

The elapsed check that guards every `execute*` call is: [2](#0-1) 

```solidity
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
```

`block.timestamp` (~1.75 × 10⁹ at the time of writing) is a `uint256`. `type(uint32).max` is 4,294,967,295. Any `timelock` value greater than `type(uint32).max − block.timestamp` (≈ 2.54 × 10⁹ s ≈ 80 years) causes the addition to exceed `uint32` range. The truncated `executeAfter` wraps to a value well below `block.timestamp`, so `_requireElapsed` passes immediately.

There is no cap on the `newTimelock` argument accepted by `proposeOracleStopLossTimelock`: [3](#0-2) 

```solidity
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    ...
}
```

`uint32.max` (4,294,967,295) is a valid argument and is accepted without revert.

---

### Impact Explanation

The timelock is the only mechanism that gives LPs advance notice before the pool admin changes stop-loss parameters. Once bypassed, the pool admin can:

1. **Disable the stop-loss** by immediately executing `drawdownE6 = E6` (100%), which sets `floorMultiplier = 0`, making `metric < (hwm * 0) / E6` always false — the guard never triggers.
2. **Maximize decay** by immediately executing `decayPerSecondE8 = E8`, causing watermarks to collapse to zero within one second, again silencing the guard.

With the stop-loss disabled, subsequent swaps can drain LP principal without the `afterSwap` hook reverting. This is a direct loss of LP-deposited token0/token1 balances. [4](#0-3) 

---

### Likelihood Explanation

The attack requires a malicious pool admin. The pool admin is explicitly a semi-trusted role whose power over LPs is bounded by the timelock — bypassing the timelock is listed as an in-scope admin-boundary break. The steps are deterministic and require no special on-chain conditions beyond the admin having already waited out the current timelock once (to set `timelock = uint32.max`). After that single wait, all future proposals execute instantly.

---

### Recommendation

Cap the timelock at a safe maximum (e.g., 365 days) inside `proposeOracleStopLossTimelock`, and perform the deadline arithmetic in `uint256` before storing:

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

Store and compare `executeAfter` as `uint256` throughout `PoolStopLossSchedule` and `_requireElapsed`.

---

### Proof of Concept

```solidity
// 1. Pool is initialized with timelock = 1 days (protecting LPs).
// 2. Admin proposes timelock = type(uint32).max.
//    _afterTimelock returns uint32(block.timestamp + 1 days) — a future timestamp.
// 3. Admin waits 1 day, executes the timelock change.
//    oracleStopLossConfig[pool].timelock = type(uint32).max.
// 4. Admin proposes drawdownE6 = E6 (disables stop-loss).
//    _afterTimelock: uint32(1_753_000_000 + 4_294_967_295)
//                  = uint32(6_047_967_295)
//                  = 6_047_967_295 % 2^32
//                  = 1_752_999_999   ← already in the past
// 5. Admin immediately calls executeOracleStopLossDrawdown.
//    _requireElapsed(1_752_999_999): block.timestamp (1_753_000_000) >= 1_752_999_999 → passes.
// 6. drawdownE6 = E6 → floorMultiplier = 0 → stop-loss permanently disabled.
// 7. Swaps that drain LP bins proceed without the afterSwap hook reverting.
``` [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L297-303)
```text
  function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
  }

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
