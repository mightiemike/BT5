### Title
`OracleValueStopLossExtension` timelock uint32 overflow allows pool admin to bypass all parameter timelocks instantly — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

### Summary

`OracleValueStopLossExtension` uses a `uint32 timelock` field and computes `executeAfter` via `uint32(block.timestamp + timelock)`. When `timelock` is set to `type(uint32).max`, the addition overflows the `uint32` cast and wraps to a timestamp already in the past, causing `_requireElapsed` to pass immediately. There is no upper-bound validation on the timelock value. A pool admin can exploit this to bypass the timelock on every subsequent parameter change (drawdown, decay, watermarks), defeating the LP-protection guarantee the extension is designed to provide.

### Finding Description

`_afterTimelock` computes the execution deadline:

```solidity
// OracleValueStopLossExtension.sol line 297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

`oracleStopLossConfig[pool_].timelock` is `uint32`. The addition is performed in `uint256` space (implicit promotion), then explicitly cast to `uint32`. In Solidity 0.8+, explicit casts truncate silently — they do **not** revert. [1](#0-0) 

When `timelock = type(uint32).max` (4,294,967,295):

```
block.timestamp (≈ 1,753,000,000)  +  4,294,967,295  =  6,047,967,295
uint32(6,047,967,295)  =  6,047,967,295 mod 4,294,967,296  ≈  1,752,999,999
```

The result is approximately `block.timestamp − 1` — already in the past. `_requireElapsed` then trivially passes:

```solidity
// line 301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
``` [2](#0-1) 

`proposeOracleStopLossTimelock` has **no validation** on `newTimelock`:

```solidity
// line 78-84
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);   // uses CURRENT timelock
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    ...
}
``` [3](#0-2) 

Compare: `_validateDrawdown` and `_validateDecay` both enforce upper bounds, but no analogous `_validateTimelock` exists:

```solidity
// lines 305-310
function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
}
function _validateDecay(uint256 decayPerSecondE8) private pure {
    if (decayPerSecondE8 > E8) revert OracleStopLossDecayTooLarge(decayPerSecondE8);
}
``` [4](#0-3) 

**Attack path:**

1. Pool is deployed with `timelock = 0` (or admin waits for the existing timelock to elapse).
2. Admin calls `proposeOracleStopLossTimelock(pool, type(uint32).max)`. Since current `timelock = 0`, `executeAfter = uint32(block.timestamp)` — immediately executable.
3. Admin calls `executeOracleStopLossTimelock(pool)`. `oracleStopLossConfig[pool].timelock` is now `type(uint32).max`.
4. Admin calls `proposeOracleStopLossDrawdown(pool, 0)`. `_afterTimelock` computes `uint32(block.timestamp + type(uint32).max)` → wraps to a past value.
5. Admin immediately calls `executeOracleStopLossDrawdown(pool)`. `_requireElapsed` passes. `drawdownE6 = 0`.
6. The stop-loss check short-circuits (`if (drawdown == 0) return;` at line 217), permanently disabling LP value protection with no delay. [5](#0-4) 

The same bypass applies to `proposeOracleStopLossDecay` and `proposeOracleStopLossHighWatermarks`.

### Impact Explanation

The `OracleValueStopLossExtension` NatDoc explicitly states: *"Drawdown and decay changes are timelocked so LPs can react."* The timelock is the sole mechanism protecting LPs from sudden stop-loss parameter changes by the pool admin. [6](#0-5) 

By bypassing the timelock, the pool admin can:
- Immediately set `drawdownE6 = 0`, disabling the stop-loss entirely. LPs who deposited expecting stop-loss protection are now exposed to unbounded value loss from oracle price moves.
- Immediately set watermarks to arbitrarily high values, causing `OracleStopLossTriggered` on every swap and bricking the pool.
- Immediately set `decayPerSecondE8 = E8` (maximum decay), collapsing all watermarks to zero within one second, then re-enabling swaps with no effective floor.

The allowed impact gate matches: **broken core pool functionality causing loss of funds** (stop-loss disabled → LP principal at risk) and **admin-boundary break: pool admin bypasses timelocks**.

### Likelihood Explanation

- Requires the pool admin to act maliciously or be compromised.
- The pool admin is a semi-trusted role constrained by the timelock; the timelock is the explicit boundary the extension enforces.
- The initial `timelock` is set at pool creation and can be `0` (as shown in tests: `_initPool(address(mockPool), 0, 0, 0)`), making step 2 immediately executable with no waiting.
- The overflow arithmetic is deterministic and requires no special conditions beyond setting `timelock = type(uint32).max`. [7](#0-6) 

### Recommendation

Add a `_validateTimelock` function with a reasonable upper bound (e.g., 30 days) and call it in both `initialize` and `proposeOracleStopLossTimelock`:

```solidity
uint32 private constant MAX_TIMELOCK = 30 days;

function _validateTimelock(uint256 timelock) private pure {
    if (timelock > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(timelock);
}
```

Call it in `initialize` alongside `_validateDrawdown`/`_validateDecay`, and in `proposeOracleStopLossTimelock` before storing the pending value. This eliminates the overflow path and caps the maximum delay to a value that cannot wrap a `uint32` cast for any realistic `block.timestamp`.

### Proof of Concept

```solidity
// Foundry test (extend OracleValueStopLossSubExtensionTest)
function test_timelockOverflowBypassesAllTimelocks() public {
    // 1. Initialize with timelock = 0 (immediately executable)
    //    Already done in setUp: _initPool(address(mockPool), 0, 0, 0)

    vm.startPrank(admin);

    // 2. Set timelock to type(uint32).max — no validation, passes
    extension.proposeOracleStopLossTimelock(address(mockPool), type(uint32).max);
    extension.executeOracleStopLossTimelock(address(mockPool));

    // 3. Confirm timelock is now type(uint32).max
    (,,uint32 tl,) = extension.oracleStopLossConfig(address(mockPool));
    assertEq(tl, type(uint32).max);

    // 4. Propose drawdown change — executeAfter wraps to the past
    extension.proposeOracleStopLossDrawdown(address(mockPool), 0);

    // 5. Execute immediately — no warp needed, timelock "elapsed"
    extension.executeOracleStopLossDrawdown(address(mockPool));

    vm.stopPrank();

    // 6. Stop-loss is now disabled
    (uint32 drawdown,,,) = extension.oracleStopLossConfig(address(mockPool));
    assertEq(drawdown, 0); // stop-loss permanently disabled, no timelock enforced
}
```

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L305-310)
```text
  function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
  }

  function _validateDecay(uint256 decayPerSecondE8) private pure {
    if (decayPerSecondE8 > E8) revert OracleStopLossDecayTooLarge(decayPerSecondE8);
```

**File:** metric-periphery/test/extensions/OracleValueStopLossSubExtension.t.sol (L49-49)
```text
    _initPool(address(mockPool), 0, 0, 0);
```
