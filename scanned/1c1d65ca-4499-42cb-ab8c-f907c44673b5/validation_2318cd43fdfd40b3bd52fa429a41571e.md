### Title
Unvalidated `timelock` in `OracleValueStopLossExtension.initialize` causes `uint32` truncation in `_afterTimelock`, making the timelock immediately bypassable by the pool admin — (`File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension.initialize` validates `drawdownE6` and `decayPerSecondE8` but applies **no validation** to the `timelock` parameter. Setting `timelock = type(uint32).max` causes `_afterTimelock` to silently truncate the `uint256` sum back to a **past timestamp**, making every timelocked parameter change immediately executable. A pool admin can exploit this to bypass the timelock that is specifically designed to protect LPs from sudden stop-loss parameter changes.

---

### Finding Description

In `OracleValueStopLossExtension.initialize`, the three decoded fields are treated asymmetrically:

```solidity
// metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol L56-62
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);   // ✓ capped at E6
_validateDecay(decayPerSecondE8); // ✓ capped at E8
// ✗ timelock: no validation whatsoever
oracleStopLossConfig[pool] = PoolStopLossConfig({
    drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
});
``` [1](#0-0) 

Every timelocked setter calls `_afterTimelock` to compute the execution deadline:

```solidity
// L297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [2](#0-1) 

The addition is performed in `uint256` (no revert), then **explicitly cast** to `uint32`. Explicit casts in Solidity 0.8 truncate silently. With `timelock = type(uint32).max = 4,294,967,295` and `block.timestamp ≈ 1,753,000,000`:

```
block.timestamp + type(uint32).max = 6,047,967,295
uint32(6,047,967,295)              = 1,752,999,999   ≈ block.timestamp − 1
```

The resulting `executeAfter` is already in the past. The guard:

```solidity
// L301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
``` [3](#0-2) 

evaluates `block.timestamp < block.timestamp − 1` → **false** → no revert. Every `execute*` call succeeds immediately.

The same unvalidated path exists in `proposeOracleStopLossTimelock`, which accepts any `uint32 newTimelock` without a cap:

```solidity
// L78-84
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    ...
}
``` [4](#0-3) 

So even if the pool is deployed with a legitimate timelock (e.g., 7 days), the admin can later propose `newTimelock = type(uint32).max`, wait the current timelock, execute the update, and from that point forward bypass every future timelock instantly.

---

### Impact Explanation

The `OracleValueStopLossExtension` timelock is the **only on-chain mechanism** protecting LPs from the pool admin silently weakening or disabling stop-loss parameters. Once bypassed:

- Admin immediately sets `drawdownE6 = E6` (100%), making `floorMultiplier = 0`; the breach condition `metric < (hwm * 0) / E6 = 0` is never true → **stop-loss fully disabled**.
- Admin immediately resets all per-bin watermarks to zero → protection erased.
- Admin immediately sets `decayPerSecondE8 = E8` (100%/s) → watermarks decay to zero within one second.

With stop-loss disabled, LPs are exposed to the full value-leak scenarios the extension was designed to block, resulting in **direct loss of LP principal** through unchecked adverse swaps.

---

### Likelihood Explanation

Requires a malicious or compromised pool admin. The pool admin is a semi-trusted role (distinct from the factory owner); the timelock exists precisely because the protocol does not fully trust the pool admin. The attack is non-obvious: a `uint32` value of `4,294,967,295` reads as "136-year timelock" to any observer, yet produces zero effective delay. The exploit is reachable through the normal `createPool` → `extensionInitData` path with no special permissions beyond pool admin.

---

### Recommendation

1. **Validate `timelock` in `initialize`** against a sensible maximum (e.g., 365 days):

```solidity
uint32 private constant MAX_TIMELOCK = 365 days;

function initialize(...) {
    ...
    if (timelock > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(timelock);
    ...
}
```

2. **Validate `newTimelock` in `proposeOracleStopLossTimelock`** with the same cap.

3. **Harden `_afterTimelock`** to revert on overflow rather than truncate:

```solidity
function _afterTimelock(address pool_) private view returns (uint32) {
    uint256 result = block.timestamp + oracleStopLossConfig[pool_].timelock;
    require(result <= type(uint32).max, "timelock overflow");
    return uint32(result);
}
```

---

### Proof of Concept

**Scenario A — exploit at initialization:**

1. Pool admin calls `factory.createPool(params)` with `extensionInitData = abi.encode(uint32(50_000), uint32(58), uint32(type(uint32).max))`.
2. Factory calls `OracleValueStopLossExtension.initialize(pool, data)`. `drawdownE6` and `decayPerSecondE8` pass validation; `timelock = type(uint32).max` is stored without check.
3. LPs observe `oracleStopLossConfig[pool].timelock = 4,294,967,295` (≈136 years) and deposit.
4. Admin calls `proposeOracleStopLossDrawdown(pool, 1e6)`.
   - `_afterTimelock` returns `uint32(block.timestamp + type(uint32).max) ≈ block.timestamp − 1`.
5. Admin immediately calls `executeOracleStopLossDrawdown(pool)`.
   - `_requireElapsed(block.timestamp − 1)` → `block.timestamp < block.timestamp − 1` → false → no revert.
6. `drawdownE6 = 1e6`; `floorMultiplier = 0`; stop-loss is permanently disabled.

**Scenario B — exploit after legitimate timelock:**

1. Pool deployed with `timelock = 7 days`. LPs deposit.
2. Admin calls `proposeOracleStopLossTimelock(pool, type(uint32).max)`.
3. After 7 days, admin calls `executeOracleStopLossTimelock(pool)` → `timelock = type(uint32).max`.
4. Admin immediately proposes and executes any parameter change (steps 4–6 above).

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
