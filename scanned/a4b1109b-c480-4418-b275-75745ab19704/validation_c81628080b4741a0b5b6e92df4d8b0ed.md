### Title
`uint32` Truncation in `_afterTimelock` Lets Pool Admin Bypass the Stop-Loss Timelock Immediately — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterTimelock` casts the sum `block.timestamp + timelock` to `uint32`. Because `timelock` is an uncapped `uint32`, an admin can set it to a value large enough that the addition wraps on truncation, producing an `executeAfter` timestamp that is already in the past. Every subsequent `_requireElapsed` check then passes immediately, letting the admin execute any pending stop-loss parameter change (drawdown, decay, watermarks) with zero waiting time.

---

### Finding Description

`_afterTimelock` computes the execution deadline for every timelocked proposal:

```solidity
// OracleValueStopLossExtension.sol line 297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [1](#0-0) 

`block.timestamp` is `uint256`; `timelock` is `uint32`. The addition is performed in `uint256` (no Solidity 0.8 revert), but the result is then **silently truncated** to `uint32`. When the sum exceeds `2^32 − 1 = 4 294 967 295`, the truncation wraps the value back below the current timestamp.

The deadline is checked by:

```solidity
// OracleValueStopLossExtension.sol line 301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
``` [2](#0-1) 

`block.timestamp` (uint256) is compared against the truncated `uint32` value. If the truncated result is less than the current timestamp, the guard passes immediately.

Neither `initialize` nor `proposeOracleStopLossTimelock` validates or caps the `timelock` value:

```solidity
// initialize — no cap on timelock
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);
_validateDecay(decayPerSecondE8);
// timelock accepted without bounds check
``` [3](#0-2) 

```solidity
// proposeOracleStopLossTimelock — no cap on newTimelock
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);   // uses CURRENT timelock
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    ...
}
``` [4](#0-3) 

The `PoolStopLossConfig.timelock` field is stored as `uint32`:

```solidity
struct PoolStopLossConfig {
    uint32 drawdownE6;
    uint32 decayPerSecondE8;
    uint32 timelock;
    bool initialized;
}
``` [5](#0-4) 

---

### Impact Explanation

The timelock is the **only** mechanism protecting LPs from sudden stop-loss parameter changes by the pool admin. The NatSpec explicitly states:

> *"Drawdown and decay changes are timelocked so LPs can react."* [6](#0-5) 

Once the timelock is bypassed, the admin can:

- **Set `drawdownE6 = 0`** — disables the stop-loss entirely, removing LP protection against oracle manipulation or value drain.
- **Set `drawdownE6 = E6` (100%)** — triggers the stop-loss on any price movement, permanently blocking all swaps and freezing LP funds.
- **Set arbitrary high watermarks** — forces the stop-loss to fire on normal price moves, again freezing the pool.
- **Set `decayPerSecondE8 = 0`** — prevents watermarks from ever decaying, making the stop-loss permanently sticky.

All of these can be executed **atomically** (propose + execute in the same block) once the overflow is in place, giving LPs no time to react and withdraw.

---

### Likelihood Explanation

The attack requires the pool admin to:

1. Propose `timelock = T_overflow` where `T_overflow > uint32_max − block.timestamp` (currently `> 2 541 967 295 s ≈ 80.6 years`). Any `uint32` value in the range `[2 541 967 296, 4 294 967 295]` triggers the overflow.
2. Wait for the **current** timelock to elapse before executing the new timelock value.
3. After that, every subsequent proposal's `executeAfter` is a past timestamp.

Step 2 is the only friction. If the pool was deployed with a short timelock (e.g., 1 day), the admin needs to wait only 1 day. The 80-year-looking timelock value is never actually enforced as a delay — it is the value that causes the overflow, not the delay the admin must wait.

The pool admin role is a **semi-trusted** actor constrained by the timelock. This is an admin-boundary break: the admin exceeds the cap the timelock is supposed to impose.

---

### Recommendation

1. **Cap the timelock** in both `initialize` and `proposeOracleStopLossTimelock` to a safe maximum (e.g., 30 days = 2 592 000 s), well below the overflow threshold:

```solidity
uint256 private constant MAX_TIMELOCK = 30 days;

function _validateTimelock(uint256 t) private pure {
    if (t > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(t);
}
```

2. **Alternatively**, widen the storage and computation to `uint64` for `timelock` and `executeAfter` fields, eliminating the truncation entirely.

---

### Proof of Concept

Concrete numbers (July 2025, `block.timestamp ≈ 1 753 000 000`):

| Step | Action | Detail |
|---|---|---|
| 1 | Admin proposes `timelock = 4 294 967 295` | Waits for current timelock (e.g., 1 day) |
| 2 | Admin executes new timelock | `oracleStopLossConfig[pool].timelock = 4 294 967 295` |
| 3 | Admin proposes `drawdownE6 = 0` | `_afterTimelock` computes `uint32(1 753 086 400 + 4 294 967 295)` = `uint32(6 048 053 695)` = `6 048 053 695 − 4 294 967 296` = **`1 753 086 399`** (1 second in the past) |
| 4 | Admin immediately calls `executeOracleStopLossDrawdown` | `_requireElapsed(1 753 086 399)`: `block.timestamp (1 753 086 400) < 1 753 086 399` → **false** → no revert |
| 5 | Stop-loss disabled | `drawdownE6 = 0`; all LP protection removed with zero effective delay |

The `executeAfter` stored in `PoolStopLossSchedule.pendingDrawdownExecuteAfter` is `uint32`, so the wrapped value is stored and compared as-is, confirming the bypass is persistent across blocks. [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L13-28)
```text
/// @title OracleValueStopLossExtension
/// @notice Tracks per-bin value per share in token0 and token1 terms at the oracle mid,
///         against decaying high watermarks. Drawdown and decay changes are timelocked so LPs
///         can react; monitor at least as often as the timelock or trust the pool admin.
/// @dev Value formulas (Q64.64 mid = token1 per token0), per-share in bin scaled units:
///
///      metricToken0 = t0*SCALE/shares + (t1 * 2^64 / mid) * SCALE / shares
///      metricToken1 = (t0 * mid / 2^64) * SCALE / shares + t1*SCALE/shares
///
///      A pure mid move pushes the metrics in opposite directions; a value leak pushes both down.
///        - metricToken0 breach (mid suspect-high) blocks zeroForOne == true  (token1 outflow)
///        - metricToken1 breach (mid suspect-low)  blocks zeroForOne == false (token0 outflow)
///        - both breached blocks both directions
///
///      Watermarks decay linearly at decayPerSecondE8 (lazy, per bin). Guarantee: value per
///      share at oracle marks cannot fall faster than drawdown (one-time) + decay * t (ongoing).
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

**File:** metric-periphery/contracts/interfaces/extensions/IOracleValueStopLossExtension.sol (L13-18)
```text
  struct PoolStopLossConfig {
    uint32 drawdownE6;
    uint32 decayPerSecondE8;
    uint32 timelock;
    bool initialized;
  }
```

**File:** metric-periphery/contracts/interfaces/extensions/IOracleValueStopLossExtension.sol (L20-27)
```text
  struct PoolStopLossSchedule {
    uint32 pendingTimelock;
    uint32 pendingTimelockExecuteAfter;
    uint32 pendingDrawdownE6;
    uint32 pendingDrawdownExecuteAfter;
    uint32 pendingDecayPerSecondE8;
    uint32 pendingDecayExecuteAfter;
  }
```
