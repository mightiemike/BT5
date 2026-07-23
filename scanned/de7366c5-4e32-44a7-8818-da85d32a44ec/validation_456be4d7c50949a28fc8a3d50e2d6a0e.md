The vulnerability is real. Let me trace it precisely.

### Title
`_afterTimelock` uint32 Truncation Allows Pool Admin to Bypass LP Protection Timelock — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`_afterTimelock` computes `uint32(block.timestamp + oracleStopLossConfig[pool_].timelock)`. When `timelock` is set to `type(uint32).max`, the `uint256` sum overflows the `uint32` range and truncates to a value in the past. `_requireElapsed` then passes immediately, letting the pool admin execute any timelocked parameter change (drawdown, decay, watermarks) without waiting, stripping LPs of their protection window.

---

### Finding Description

The vulnerable helper:

```solidity
// OracleValueStopLossExtension.sol line 297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [1](#0-0) 

The guard that enforces the delay:

```solidity
// line 301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
``` [2](#0-1) 

`block.timestamp` is `uint256`; `timelock` is `uint32`. The addition is performed in `uint256` space, then **truncated** to `uint32`. With `timelock = type(uint32).max` (4,294,967,295) and current `block.timestamp` ≈ 1,753,142,400 (July 2026):

```
sum = 1,753,142,400 + 4,294,967,295 = 6,048,109,695
uint32(6,048,109,695) = 6,048,109,695 mod 4,294,967,296 = 1,753,142,399
```

`executeAfter` = 1,753,142,399 — **one second in the past**. `_requireElapsed` checks `block.timestamp < executeAfter` → `1,753,142,400 < 1,753,142,399` → **false** → no revert → immediate execution.

The pool admin can reach this state through the normal timelocked flow:

1. Call `proposeOracleStopLossTimelock(pool, type(uint32).max)` — proposes the extreme timelock value.
2. Wait for the **current** timelock to elapse.
3. Call `executeOracleStopLossTimelock(pool)` — stores `timelock = type(uint32).max`.
4. Call `proposeOracleStopLossDrawdown(pool, newDrawdown)` — `_afterTimelock` wraps to a past timestamp, stored in `pendingDrawdownExecuteAfter`.
5. Call `executeOracleStopLossDrawdown(pool)` **immediately** — `_requireElapsed` passes because `executeAfter` is already in the past.

The same path applies to `proposeOracleStopLossDecay` and `proposeOracleStopLossHighWatermarks`, which all call `_afterTimelock`. [3](#0-2) [4](#0-3) 

---

### Impact Explanation

The NatDoc explicitly states: *"Drawdown and decay changes are timelocked so LPs can react."* [5](#0-4) 

By bypassing the timelock, the pool admin can:

- **Immediately increase `drawdownE6`** (e.g., from 5% to 100%), disabling the stop-loss guard entirely without giving LPs any window to exit.
- **Immediately lower `decayPerSecondE8`** to zero, freezing watermarks at stale highs and making the guard permanently ineffective.
- **Immediately set watermarks** to artificially high values, causing the guard to block legitimate swaps or to never trigger on real value loss.

The direct consequence is that LPs lose the protection window the timelock was designed to provide. A pool admin who has turned adversarial (or whose key is compromised) can drain LP value through bad-price swaps that the now-disabled stop-loss would otherwise have blocked. This is an explicit admin-boundary break: the pool admin exceeds the cap imposed by the timelock mechanism.

---

### Likelihood Explanation

The path requires the pool admin to first execute a timelock change to `type(uint32).max` (one round-trip through the existing timelock). After that, every subsequent parameter proposal is immediately executable. No external conditions, oracle state, or user interaction are needed. The `timelock` field is a `uint32` with no upper-bound validation, so `type(uint32).max` is an accepted value. [6](#0-5) 

---

### Recommendation

Perform the addition in `uint256` and validate before truncating, or cap `timelock` to a safe maximum:

```solidity
function _afterTimelock(address pool_) private view returns (uint32) {
    uint256 result = block.timestamp + uint256(oracleStopLossConfig[pool_].timelock);
    require(result <= type(uint32).max, "timelock overflow");
    return uint32(result);
}
```

Alternatively, add a `MAX_TIMELOCK` constant (e.g., 365 days ≈ 31,536,000 seconds, well within `uint32`) and enforce it in `initialize` and `executeOracleStopLossTimelock`.

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_timelockUint32WrapBypass() public {
    // Step 1: pool starts with timelock = 0 (immediate execution allowed for timelock change)
    // Step 2: propose and execute timelock = type(uint32).max
    vm.startPrank(admin);
    extension.proposeOracleStopLossTimelock(address(pool), type(uint32).max);
    extension.executeOracleStopLossTimelock(address(pool)); // passes: current timelock is 0

    // Step 3: propose a drawdown change
    extension.proposeOracleStopLossDrawdown(address(pool), 1_000_000); // 100% drawdown = disable guard

    // Step 4: executeAfter has wrapped to a past timestamp — execute immediately
    extension.executeOracleStopLossDrawdown(address(pool)); // succeeds without any warp

    (uint32 dd,,,) = extension.oracleStopLossConfig(address(pool));
    assertEq(dd, 1_000_000); // guard disabled, timelock bypassed
    vm.stopPrank();
}
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L13-17)
```text
/// @title OracleValueStopLossExtension
/// @notice Tracks per-bin value per share in token0 and token1 terms at the oracle mid,
///         against decaying high watermarks. Drawdown and decay changes are timelocked so LPs
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L103-109)
```text
  function proposeOracleStopLossDrawdown(address pool_, uint256 newMaxDrawdownE6) external onlyPoolAdmin(pool_) {
    _validateDrawdown(newMaxDrawdownE6);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDrawdownE6 = uint32(newMaxDrawdownE6);
    sched.pendingDrawdownExecuteAfter = executeAfter;
    emit OracleStopLossDrawdownProposed(pool_, newMaxDrawdownE6, executeAfter);
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
