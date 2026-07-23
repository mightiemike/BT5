### Title
`uint32` Overflow in `_afterTimelock` Silently Produces a Past `executeAfter`, Letting the Pool Admin Bypass the Stop-Loss Timelock Immediately — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterTimelock` computes the proposal deadline as `uint32(block.timestamp + oracleStopLossConfig[pool_].timelock)`. When `timelock` is set to any value greater than `type(uint32).max − block.timestamp` (≈ 2.54 billion seconds / ~80 years at current timestamps), the addition overflows the `uint32` cast and wraps to a timestamp already in the past. Every subsequent `propose*` call stores this wrapped value as `executeAfter`, and `_requireElapsed` passes immediately, so the pool admin can execute any stop-loss parameter change in the same block as the proposal — with zero LP reaction time.

---

### Finding Description

`_afterTimelock` is the single source of `executeAfter` for all four proposal paths:

```
proposeOracleStopLossTimelock      → executeAfter = _afterTimelock(pool_)
proposeOracleStopLossDrawdown      → executeAfter = _afterTimelock(pool_)
proposeOracleStopLossDecay         → executeAfter = _afterTimelock(pool_)
proposeOracleStopLossHighWatermarks→ executeAfter = _afterTimelock(pool_)
```

The vulnerable line:

```solidity
// OracleValueStopLossExtension.sol line 297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

`block.timestamp` is `uint256`; `oracleStopLossConfig[pool_].timelock` is `uint32`. The addition is performed in `uint256` space, then silently truncated to `uint32`. No cap or validation is applied to the `timelock` field — neither in `initialize` nor in `proposeOracleStopLossTimelock`:

```solidity
// initialize — no timelock validation
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);   // only drawdown and decay are validated
_validateDecay(decayPerSecondE8);
```

```solidity
// proposeOracleStopLossTimelock — no cap on newTimelock
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    ...
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
```

The guard that is supposed to enforce the delay:

```solidity
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
```

When `executeAfter` wraps to a value already less than `block.timestamp`, this check passes unconditionally.

**Overflow arithmetic (July 2025):**

| Variable | Value |
|---|---|
| `block.timestamp` | ≈ 1,753,000,000 |
| `type(uint32).max` | 4,294,967,295 |
| Overflow threshold for `timelock` | > 2,541,967,295 s (≈ 80.6 years) |
| `block.timestamp + type(uint32).max` | 6,047,967,295 |
| `uint32(6,047,967,295)` | **1,752,999,999** (1 second in the past) |

---

### Impact Explanation

The timelock is the sole LP-protection mechanism against sudden stop-loss parameter changes by the pool admin. With the overflow active, the pool admin can:

1. Propose `drawdownE6 = 0` (disabling the stop-loss entirely).
2. Call `executeOracleStopLossDrawdown` in the **same block**.
3. The stop-loss is now silently disabled; value-leaking swaps that would previously have been blocked by `OracleStopLossTriggered` now execute freely.
4. LPs receive no advance notice and cannot withdraw before their position is drained.

This is a direct LP principal loss path gated only on the pool admin being willing to set an overflowing timelock value — an admin-boundary break where the pool admin bypasses a timelock that LPs were relying on.

---

### Likelihood Explanation

The pool admin must first install an overflowing `timelock` value. This requires one honest wait through the current timelock (to execute the timelock-change proposal). After that single wait, **all future proposals on that pool are immediately executable** with no further delay. The deceptive aspect mirrors the external bug: a `timelock` of `type(uint32).max` (≈ 136 years) appears to be an extremely conservative setting, yet it produces an `executeAfter` that is already in the past.

---

### Recommendation

Cast `block.timestamp` to `uint32` **before** adding the timelock, mirroring the fix in the referenced external report:

```diff
 function _afterTimelock(address pool_) private view returns (uint32) {
-    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
+    return uint32(block.timestamp) + oracleStopLossConfig[pool_].timelock;
 }
```

Additionally, add an upper-bound validation on `timelock` in both `initialize` and `proposeOracleStopLossTimelock` (e.g., `timelock <= type(uint32).max - type(uint32).max / 2`) to prevent any future overflow path.

---

### Proof of Concept

```solidity
// Append to OracleValueStopLossSubExtension.t.sol

function test_timelockOverflowBypassesDelay() public {
    // Pool initialized with a 7-day timelock (normal protective setting).
    OracleValueStopLossExtension freshExt = new OracleValueStopLossExtension(address(factoryStub));
    MockExtensionExtsloadPool freshPool = new MockExtensionExtsloadPool(address(factoryStub), MIN_SHARES);
    factoryStub.setPoolAdmin(address(freshPool), admin);
    vm.prank(address(factoryStub));
    freshExt.initialize(address(freshPool), abi.encode(uint32(500_000), uint32(0), uint32(7 days)));

    vm.startPrank(admin);

    // Step 1: propose timelock = type(uint32).max (looks like 136-year protection).
    freshExt.proposeOracleStopLossTimelock(address(freshPool), type(uint32).max);
    // Must wait the current 7-day timelock once.
    vm.warp(block.timestamp + 7 days);
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
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L103-110)
```text
  function proposeOracleStopLossDrawdown(address pool_, uint256 newMaxDrawdownE6) external onlyPoolAdmin(pool_) {
    _validateDrawdown(newMaxDrawdownE6);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDrawdownE6 = uint32(newMaxDrawdownE6);
    sched.pendingDrawdownExecuteAfter = executeAfter;
    emit OracleStopLossDrawdownProposed(pool_, newMaxDrawdownE6, executeAfter);
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
