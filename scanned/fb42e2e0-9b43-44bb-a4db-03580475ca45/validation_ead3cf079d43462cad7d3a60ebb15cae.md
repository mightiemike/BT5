### Title
Unvalidated `timelockSeconds` in `OracleValueStopLossExtension.initialize` lets pool admin atomically disable LP stop-loss protection — (`File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

### Summary

`OracleValueStopLossExtension.initialize` validates `drawdownE6` and `decayPerSecondE8` but accepts any value — including zero — for `timelockSeconds`. A pool deployed with `timelock = 0` lets the pool admin propose **and** execute drawdown/decay changes in the same block, collapsing the LP-reaction window to zero and silently disabling the stop-loss guard.

### Finding Description

The `initialize` function decodes three parameters and validates only two of them:

```solidity
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);   // ✓ checked
_validateDecay(decayPerSecondE8); // ✓ checked
// timelock — NOT checked
oracleStopLossConfig[pool] = PoolStopLossConfig({
    drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
});
``` [1](#0-0) 

The timelock is consumed by two helpers:

```solidity
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
``` [2](#0-1) 

When `timelock = 0`, `_afterTimelock` returns exactly `block.timestamp`. The guard `block.timestamp < block.timestamp` is always `false`, so `_requireElapsed` never reverts. Every propose/execute pair can be called atomically in a single transaction.

The same block-level bypass applies to all three timelocked parameters: `drawdownE6`, `decayPerSecondE8`, and the watermarks themselves.

The stop-loss is fully disabled when `drawdownE6` is raised to `1e6` (100 %):

```solidity
function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
}
``` [3](#0-2) 

`drawdownE6 = 1e6` is accepted (only `> E6` reverts). With `floorMultiplier = E6 − 1e6 = 0`, the breach condition becomes `metric < 0`, which is impossible for a `uint256`, so `OracleStopLossTriggered` is never emitted:

```solidity
breached = metric < (hwm * floorMultiplier) / E6;  // hwm * 0 / 1e6 == 0
``` [4](#0-3) 

The NatSpec explicitly promises the opposite behaviour:

> *"Drawdown and decay changes are timelocked so LPs can react; monitor at least as often as the timelock or trust the pool admin."* [5](#0-4) 

The existing test `test_decayTimelockZeroExecutesImmediately` confirms the zero-timelock path executes without delay — it is treated as a feature, not a bug, yet no floor is enforced: [6](#0-5) 

### Impact Explanation

LPs who deposit into a pool advertising the stop-loss extension expect a minimum reaction window before the pool admin can weaken or remove the guard. With `timelock = 0` the admin can, in a single atomic transaction:

1. Raise `drawdownE6` to `1_000_000` (100 %) — stop-loss threshold collapses to 0.
2. Raise `decayPerSecondE8` to `1e8` (100 %/s) — watermarks decay to zero within one second, permanently re-arming the ratchet at whatever value the next swap sets.

After either action the stop-loss no longer blocks any swap direction, and LP principal is exposed to oracle-price manipulation or any other value-extraction path the extension was meant to prevent. This is a direct loss of LP principal above Sherlock thresholds.

### Likelihood Explanation

Medium. The pool admin is semi-trusted and controls the `createPool` call that supplies the `initialize` data. A pool admin who wishes to attract LP deposits under the appearance of stop-loss protection, then silently remove it, can do so without any on-chain friction. No external oracle manipulation or contract exploit is required — only the admin's own transactions.

### Recommendation

**Short term:** Add a minimum-timelock check inside `initialize` (and inside `executeOracleStopLossTimelock` when the new value is applied):

```solidity
uint32 internal constant MIN_TIMELOCK = 1 hours;

function _validateTimelock(uint32 timelock) private pure {
    if (timelock < MIN_TIMELOCK) revert OracleStopLossTimelockTooShort(timelock);
}
```

Call `_validateTimelock(timelock)` alongside the existing `_validateDrawdown` / `_validateDecay` calls in `initialize`, and repeat it in `executeOracleStopLossTimelock` before writing the new value.

**Long term:** Mirror the pattern already used for `drawdownE6` and `decayPerSecondE8` — validate every configuration variable that guards LP funds, including the timelock itself. Also tighten `_validateDrawdown` to reject `drawdownE6 == E6` (100 %), which silently disables the guard even when the timelock is respected.

### Proof of Concept

```solidity
// Pool admin deploys pool with stop-loss extension, timelock = 0
bytes memory initData = abi.encode(
    uint32(50_000),   // 5% drawdown — looks protective
    uint32(58),       // normal decay
    uint32(0)         // ← no validation, accepted silently
);
factory.createPool(..., extensionInitData: initData);

// LPs deposit, trusting the 5% stop-loss

// Admin atomically disables the guard in one tx:
extension.proposeOracleStopLossDrawdown(pool, 1_000_000); // 100%
extension.executeOracleStopLossDrawdown(pool);            // passes: block.timestamp < block.timestamp == false

// drawdownE6 == 1e6 → floorMultiplier == 0 → breach condition is metric < 0 → never true
// Stop-loss is now permanently disabled; LP funds are unprotected
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L15-16)
```text
///         against decaying high watermarks. Drawdown and decay changes are timelocked so LPs
///         can react; monitor at least as often as the timelock or trust the pool admin.
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L297-303)
```text
  function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
  }

  function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(executeAfter, block.timestamp);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L305-307)
```text
  function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L334-334)
```text
    breached = metric < (hwm * floorMultiplier) / E6;
```

**File:** metric-periphery/test/extensions/OracleValueStopLossSubExtension.t.sol (L249-255)
```text
  function test_decayTimelockZeroExecutesImmediately() public {
    vm.startPrank(admin);
    extension.proposeOracleStopLossDecay(address(mockPool), 58);
    extension.executeOracleStopLossDecay(address(mockPool));
    vm.stopPrank();
    assertEq(_decay(), 58);
  }
```
