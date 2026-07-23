### Title
Unbounded `timelock` in `OracleValueStopLossExtension` Allows Pool Admin to Bypass Timelock via uint32 Overflow — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

The `timelock` parameter in `OracleValueStopLossExtension` is accepted as a raw `uint32` with no upper-bound validation in either `initialize()` or `proposeOracleStopLossTimelock()`. The helper `_afterTimelock` computes `uint32(block.timestamp + timelock)`. When `timelock` is set to a value that causes this sum to exceed `type(uint32).max`, the result silently wraps to a timestamp already in the past. Every subsequent `_requireElapsed` check then passes immediately, letting the pool admin execute any parameter change — drawdown, decay, watermarks, or the timelock itself — without waiting, defeating the LP-protection guarantee the timelock is designed to enforce.

---

### Finding Description

**Root cause — no upper-bound validation on `timelock`:**

`initialize()` validates `drawdownE6` and `decayPerSecondE8` but skips `timelock`:

```solidity
// OracleValueStopLossExtension.sol lines 56-58
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);
_validateDecay(decayPerSecondE8);
// ← no _validateTimelock(timelock)
```

`proposeOracleStopLossTimelock` similarly accepts any `uint32` without validation:

```solidity
// lines 78-84
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    ...
}
```

**Overflow in `_afterTimelock`:**

```solidity
// lines 297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

`block.timestamp` is `uint256` (~1.753 × 10⁹ today). The addition is performed in `uint256` space, then **truncated** to `uint32` (Solidity 0.8 truncates explicit casts; it does not revert). `type(uint32).max` = 4,294,967,295. Any `timelock > 4,294,967,295 − 1,753,000,000 ≈ 2,541,967,295` (~80.5 years, still a valid `uint32`) causes the sum to exceed `type(uint32).max`, wrapping `executeAfter` to a value already in the past.

**Bypass of `_requireElapsed`:**

```solidity
// lines 301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(executeAfter, block.timestamp);
}
```

With `executeAfter` wrapping to, e.g., 458,032,704 (year 1984) while `block.timestamp ≈ 1,753,000,000`, the condition `block.timestamp < executeAfter` is false and the check passes immediately for every proposal.

**All timelocked operations are affected:** `executeOracleStopLossDrawdown`, `executeOracleStopLossDecay`, `executeOracleStopLossHighWatermarks`, and `executeOracleStopLossTimelock` all call `_requireElapsed` with the value produced by `_afterTimelock`.

---

### Impact Explanation

The timelock is the sole mechanism protecting LPs from sudden, unannounced parameter changes by the pool admin. With the bypass active, the pool admin can:

1. **Instantly disable the stop-loss** by setting `drawdownE6 = 0`, removing all value-leak protection without any LP reaction window.
2. **Instantly raise watermarks to extreme values**, causing the stop-loss to trigger on the very next swap and permanently blocking one or both swap directions.
3. **Instantly lower decay to zero**, freezing watermarks at their current peak and making the stop-loss permanently hair-trigger.

Each of these actions can cause LPs to suffer losses they would have avoided had the timelock functioned correctly — either by removing the guard that was supposed to stop value leakage, or by weaponising the guard to freeze the pool. This is a direct admin-boundary break: the pool admin exceeds the cap the timelock is designed to impose.

---

### Likelihood Explanation

The pool admin is a semi-trusted role. The attack does not require malicious initial setup: a pool can launch with a short, reasonable timelock (e.g., 1 day). The admin then proposes `newTimelock = 3,000,000,000` (a valid `uint32`), waits the current 1-day timelock, executes it, and from that point forward every proposal is immediately executable. No external preconditions, no special tokens, no oracle manipulation required.

---

### Recommendation

Add an upper-bound check for `timelock` in both `initialize()` and `proposeOracleStopLossTimelock()`, mirroring the existing guards for `drawdownE6` and `decayPerSecondE8`:

```solidity
uint32 private constant MAX_TIMELOCK = 30 days; // e.g. 30 days

function _validateTimelock(uint256 t) private pure {
    if (t > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(t);
}
```

Apply `_validateTimelock(timelock)` in `initialize()` after decoding, and