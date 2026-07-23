### Title
Unbounded `timelock` in `OracleValueStopLossExtension._afterTimelock` overflows `uint32`, allowing pool admin to immediately bypass LP-protection timelock — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension` stores a per-pool `timelock` (seconds) as `uint32` and computes the execution deadline with `uint32(block.timestamp + timelock)`. No upper-bound is validated on `timelock` at initialization or when it is updated. When `block.timestamp + timelock > type(uint32).max`, the cast silently wraps to a past timestamp, making every subsequent parameter proposal (drawdown, decay, watermarks) immediately executable — bypassing the LP-protection delay entirely.

---

### Finding Description

`_afterTimelock` is the single function that computes when a pending change may be executed:

```solidity
// OracleValueStopLossExtension.sol L297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

`block.timestamp` on mainnet is currently ≈ 1 753 000 000. `type(uint32).max` = 4 294 967 295. Any `timelock ≥ 2 541 967 296` (≈ 80.6 years, a valid `uint32`) causes the addition to exceed `type(uint32).max`; the truncation to `uint32` wraps the result to a value **smaller than the current timestamp**.

`_requireElapsed` then compares the wrapped value against `block.timestamp`:

```solidity
// OracleValueStopLossExtension.sol L301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
```

Because `block.timestamp` (uint256) is always larger than the wrapped `executeAfter` (a small uint32 value), the check passes immediately — the timelock is silently nullified.

Neither `initialize` nor `proposeOracleStopLossTimelock` validates an upper bound on `timelock`:

```solidity
// initialize() L56-62 — validates drawdown and decay, NOT timelock
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);
_validateDecay(decayPerSecondE8);
// timelock stored without any bound check
```

```solidity
// proposeOracleStopLossTimelock() L78-84 — no validation on newTimelock
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);   // uses current (still-valid) timelock
    sched.pendingTimelock = newTimelock;            // newTimelock = type(uint32).max accepted
    sched.pendingTimelockExecuteAfter = executeAfter;
    ...
}
```

---

### Impact Explanation

The `OracleValueStopLossExtension` timelock is the **only mechanism** that gives LPs time to react before the pool admin changes the drawdown floor or decay rate. Once the timelock is set to an overflow value, the pool admin can:

1. Immediately propose and execute `drawdown = E6` (100 %) — `_validateDrawdown` allows this.
2. With `drawdown = E6`, `floorMultiplier = E6 − E6 = 0`, so `_applyWatermark` evaluates `metric < (hwm * 0) / E6 = 0`, which is never true for any `uint256` metric. The stop-loss guard is permanently disabled.
3. LPs' principal is now unprotected against oracle-price manipulation or value leakage; the stop-loss that was supposed to block further outflows never fires.

This matches the allowed impact gate: **Admin-boundary break — pool admin bypasses timelocks** and **broken core pool functionality causing loss of LP assets**.

---

### Likelihood Explanation

The pool admin is a semi-trusted role. The NatDoc explicitly states the timelock exists so "LPs can react" — implying LPs may not fully trust the pool admin. A pool admin who sets `timelock = type(uint32).max` (which reads as "136-year timelock" to an LP) actually holds a zero-delay override. The trigger requires only a single admin call (`proposeOracleStopLossTimelock` with an overflow value, then `executeOracleStopLossTimelock` after the current timelock elapses), which is a reachable, semi-trusted path.

---

### Recommendation

Add an upper-bound check on `timelock` in both `initialize` and `proposeOracleStopLossTimelock`, analogous to the existing `_validateDrawdown` and `_validateDecay` guards:

```solidity
uint32 private constant MAX_TIMELOCK = 30 days; // e.g. 2_592_000 seconds

function _validateTimelock(uint32 timelock) private pure {
    if (timelock > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(timelock);
}
```

Apply `_validateTimelock` in `initialize` alongside the existing validators, and at the top of `proposeOracleStopLossTimelock` before storing `newTimelock`.

---

### Proof of Concept

```
block.timestamp ≈ 1_753_000_000 (mainnet, July 2025)
type(uint32).max = 4_294_967_295

Step 1 — pool initialized with timelock = 7 days (reasonable).

Step 2 — pool admin calls:
  proposeOracleStopLossTimelock(pool, type(uint32).max)
  // executeAfter = uint32(1_753_000_000 + 7 days) = valid future ts

Step 3 — after 7 days, pool admin calls:
  executeOracleStopLossTimelock(pool)
  // oracleStopLossConfig[pool].timelock = type(uint32).max

Step 4 — pool admin calls:
  proposeOracleStopLossDrawdown(pool, 1_000_000)  // drawdown = E6 = 100%
  // _afterTimelock() = uint32(1_753_604_800 + 4_294_967_295)
  //                  = uint32(6_048_572_095)
  //                  = uint32(6_048_572_095 % 2^32)
  //                  = 1_753_604_799   ← PAST timestamp
  // executeAfter stored = 1_753_604_799

Step 5 — pool admin immediately calls:
  executeOracleStopLossDrawdown(pool)
  // _requireElapsed(1_753_604_799):
  //   block.timestamp (≈1_753_604_800) < 1_753_604_799 → FALSE → no revert
  // drawdown set to 1_000_000 (100%) — stop-loss disabled

LPs have zero blocks to react; the stop-loss guard is permanently open.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L305-310)
```text
  function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
  }

  function _validateDecay(uint256 decayPerSecondE8) private pure {
    if (decayPerSecondE8 > E8) revert OracleStopLossDecayTooLarge(decayPerSecondE8);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L328-336)
```text
  function _applyWatermark(uint256 metric, uint256 hwm, uint256 floorMultiplier)
    private
    pure
    returns (uint256 newHwm, bool breached)
  {
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
    return (hwm, breached);
  }
```

**File:** metric-periphery/contracts/interfaces/extensions/IOracleValueStopLossExtension.sol (L13-18)
```text
  struct PoolStopLossConfig {
    uint32 drawdownE6;
    uint32 decayPerSecondE8;
    uint32 timelock;
    bool initialized;
  }
```
