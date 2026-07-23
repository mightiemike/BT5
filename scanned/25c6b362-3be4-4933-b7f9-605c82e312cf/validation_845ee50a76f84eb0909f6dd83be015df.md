### Title
`_afterTimelock` uint32 truncation allows pool admin to bypass LP reaction window — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`_afterTimelock` computes `uint32(block.timestamp + timelock)`. When `timelock = type(uint32).max`, the intermediate `uint256` sum exceeds `2^32`, and the truncation wraps the result to a timestamp in the past. `_requireElapsed` then passes immediately for every subsequent timelocked operation, giving LPs zero reaction time.

---

### Finding Description

`_afterTimelock` is:

```solidity
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [1](#0-0) 

`_requireElapsed` checks:

```solidity
if (block.timestamp < executeAfter) revert ...
``` [2](#0-1) 

Neither `initialize` nor `proposeOracleStopLossTimelock` validates the `timelock` value — only `drawdownE6` and `decayPerSecondE8` are validated:

```solidity
_validateDrawdown(drawdownE6);
_validateDecay(decayPerSecondE8);
// no _validateTimelock
``` [3](#0-2) 

```solidity
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
``` [4](#0-3) 

**Overflow arithmetic (July 2026):**

| Value | Amount |
|---|---|
| `block.timestamp` | ≈ 1,753,000,000 |
| `type(uint32).max` | 4,294,967,295 |
| Sum (uint256) | 6,047,967,295 |
| `uint32(sum)` | 6,047,967,295 mod 2³² ≈ **1,752,999,999** |

`executeAfter ≈ block.timestamp − 1`, so `_requireElapsed` passes immediately.

---

### Impact Explanation

The pool admin can:

1. Start with `timelock = 0` (no validation, perfectly valid initial value).
2. Call `proposeOracleStopLossTimelock(pool, type(uint32).max)` — since current timelock is 0, `_afterTimelock` returns `uint32(block.timestamp)`, already elapsed.
3. Immediately call `executeOracleStopLossTimelock` — succeeds, sets `timelock = type(uint32).max`.
4. Call `proposeOracleStopLossDrawdown(pool, 0)` — `_afterTimelock` now wraps to a past timestamp.
5. Immediately call `executeOracleStopLossDrawdown` — `_requireElapsed` passes, drawdown set to 0.

The stop-loss is now disabled with zero LP reaction time. The same bypass applies to `proposeOracleStopLossDecay` and `proposeOracleStopLossHighWatermarks`, all of which call `_afterTimelock`: [5](#0-4) [6](#0-5) [7](#0-6) 

This is an explicit admin-boundary break: the pool admin exceeds their intended capability by nullifying the LP reaction window that the timelock mechanism is designed to enforce.

---

### Likelihood Explanation

The pool admin is a semi-trusted role constrained by timelocks. A pool starting with `timelock = 0` (a common default) can be exploited in two transactions. No privileged factory or oracle role is needed beyond the pool admin role itself. The exploit is fully on-chain and deterministic.

---

### Recommendation

Add a maximum cap on the timelock value in both `initialize` and `proposeOracleStopLossTimelock`, and validate that `block.timestamp + timelock` does not overflow `uint32`:

```solidity
uint256 private constant MAX_TIMELOCK = type(uint32).max / 2; // ~68 years, safe from overflow

function _validateTimelock(uint256 timelock) private pure {
    if (timelock > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(timelock);
}
```

Apply `_validateTimelock` in `initialize` alongside the existing validators, and in `proposeOracleStopLossTimelock` before storing `newTimelock`.

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_timelockOverflowBypassesLPWindow() public {
    // Pool initialized with timelock = 0
    // Step 1: set timelock to type(uint32).max (passes immediately since current timelock = 0)
    vm.prank(admin);
    extension.proposeOracleStopLossTimelock(pool, type(uint32).max);
    vm.prank(admin);
    extension.executeOracleStopLossTimelock(pool); // succeeds: executeAfter = block.timestamp

    // Step 2: propose drawdown change — _afterTimelock wraps to past
    vm.prank(admin);
    extension.proposeOracleStopLossDrawdown(pool, 0); // disable stop-loss

    // Step 3: execute immediately — no warp needed
    vm.prank(admin);
    extension.executeOracleStopLossDrawdown(pool); // succeeds despite zero elapsed time

    assertEq(extension.oracleStopLossConfig(pool).drawdownE6, 0);
}
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L57-58)
```text
    _validateDrawdown(drawdownE6);
    _validateDecay(decayPerSecondE8);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L78-78)
```text
  function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L106-108)
```text
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDrawdownE6 = uint32(newMaxDrawdownE6);
    sched.pendingDrawdownExecuteAfter = executeAfter;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L133-135)
```text
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDecayPerSecondE8 = uint32(newDecayPerSecondE8);
    sched.pendingDecayExecuteAfter = executeAfter;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L162-164)
```text
    uint32 executeAfter = _afterTimelock(pool_);
    pendingHighWatermark[pool_] =
      PendingHighWatermarks({token0: newHwmToken0, token1: newHwmToken1, binIdx: binIdx, executeAfter: executeAfter});
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
