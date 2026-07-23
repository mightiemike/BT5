The vulnerability is real. Let me trace through the exact code path to confirm.

### Title
uint32 Truncation in `_afterTimelock` Allows Pool Admin to Bypass Timelock and Eliminate LP Reaction Window — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`_afterTimelock` casts `block.timestamp + timelock` (a `uint256` sum) to `uint32`. When the pool admin sets `timelock` to a value large enough that the sum exceeds `type(uint32).max`, the truncation wraps `executeAfter` to a timestamp in the past. `_requireElapsed` then passes immediately, letting the admin execute any timelocked parameter change (drawdown, decay, watermarks) with zero delay.

---

### Finding Description

The vulnerable function:

```solidity
// OracleValueStopLossExtension.sol L297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

The addition is performed in `uint256` space (no overflow), then silently truncated to `uint32`. The guard:

```solidity
// L301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
```

compares `uint256 block.timestamp` against the truncated `uint32 executeAfter` (implicitly widened back to `uint256`). If `executeAfter` wrapped to a value below `block.timestamp`, the revert is never triggered.

There is no `_validateTimelock` — unlike drawdown and decay, the timelock value has no upper-bound check:

```solidity
// L305-310 — drawdown and decay are validated; timelock is not
function _validateDrawdown(uint256 drawdownE6) private pure { ... }
function _validateDecay(uint256 decayPerSecondE8) private pure { ... }
// no _validateTimelock exists
```

**Concrete arithmetic (July 2026):**

| Variable | Value |
|---|---|
| `block.timestamp` | ≈ 1,753,000,000 |
| `timelock` (set by admin) | `type(uint32).max` = 4,294,967,295 |
| Sum (uint256) | 6,047,967,295 |
| `uint32(sum)` | 6,047,967,295 − 4,294,967,296 = **1,752,999,999** |
| `block.timestamp < executeAfter`? | 1,753,000,000 < 1,752,999,999 → **false** |

`_requireElapsed` does not revert; the timelock is fully bypassed.

---

### Impact Explanation

The timelock is the sole mechanism protecting LPs from sudden pool admin parameter changes. The NatSpec states explicitly: *"Drawdown and decay changes are timelocked so LPs can react."* By bypassing it, the pool admin can:

- Immediately raise `drawdownE6` to `1e6` (100%), disabling all stop-loss protection.
- Immediately lower `drawdownE6` to 1, causing every subsequent swap to revert with `OracleStopLossTriggered`, bricking the pool.
- Immediately reset high watermarks to zero, making the stop-loss fire on the next swap.

LPs have no window to observe the pending change and exit before it takes effect. This is a direct admin-boundary break: the pool admin exceeds the constraint the protocol imposes on their power.

---

### Likelihood Explanation

The pool admin is a semi-trusted role explicitly constrained by timelocks. The exploit requires only two admin transactions:

1. `proposeOracleStopLossTimelock(pool, type(uint32).max)` — executable immediately if the current timelock is 0 (valid initial value, no minimum enforced), or after the current timelock elapses otherwise.
2. Any `proposeOracleStopLoss*` call followed immediately by the corresponding `execute*` call.

No external oracle manipulation, no special token behavior, and no factory-owner privilege is needed.

---

### Recommendation

Add an upper-bound validation for the timelock, mirroring the existing pattern for drawdown and decay:

```solidity
uint32 private constant MAX_TIMELOCK = 30 days; // example cap

function _validateTimelock(uint32 timelock) private pure {
    if (timelock > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(timelock);
}
```

Call `_validateTimelock` in both `initialize` and `proposeOracleStopLossTimelock`. Alternatively, perform the addition in checked arithmetic or use `SafeCast` before truncating to `uint32`.

---

### Proof of Concept

```solidity
// Foundry test — add to OracleValueStopLossSubExtension.t.sol
function test_timelockOverflowBypassesReactionWindow() public {
    // Step 1: pool initialized with timelock = 0 (no minimum enforced)
    // setUp() already calls _initPool with timelock=0

    vm.startPrank(admin);

    // Step 2: set timelock to type(uint32).max — passes immediately (current timelock = 0)
    extension.proposeOracleStopLossTimelock(address(mockPool), type(uint32).max);
    extension.executeOracleStopLossTimelock(address(mockPool));

    // Step 3: configure a non-zero drawdown so the stop-loss is active
    _configure(50_000, 0); // sets drawdownE6 directly via internal helper

    // Step 4: propose a drawdown change — _afterTimelock wraps to past timestamp
    extension.proposeOracleStopLossDrawdown(address(mockPool), 1_000);

    // Step 5: execute immediately — should revert if timelock worked, but it doesn't
    // No vm.warp needed; zero elapsed time
    extension.executeOracleStopLossDrawdown(address(mockPool));

    // Assert: drawdown changed with zero delay, LP reaction window was zero
    assertEq(extension.oracleStopLossConfig(address(mockPool)).drawdownE6, 1_000);

    vm.stopPrank();
}
```

**Key assertions:** `executeOracleStopLossDrawdown` succeeds in the same block as `proposeOracleStopLossDrawdown`, violating `TIMELOCK_MUST_DELAY_EXECUTION_BY_CONFIGURED_SECONDS`.

---

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/interfaces/extensions/IOracleValueStopLossExtension.sol (L13-18)
```text
  struct PoolStopLossConfig {
    uint32 drawdownE6;
    uint32 decayPerSecondE8;
    uint32 timelock;
    bool initialized;
  }
```
