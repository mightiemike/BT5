The core logic is clear. Let me verify the exact validation boundary and the `_applyWatermark` arithmetic one more time before rendering the verdict.

**Key facts established from the code:**

1. `_validateDrawdown` uses strict `>` — so `drawdownE6 == 1e6` passes: [1](#0-0) 

2. `floorMultiplier = E6 - drawdown` — when `drawdown == 1e6`, `floorMultiplier = 0`: [2](#0-1) 

3. The early-exit guard only fires on `drawdown == 0`, not on `drawdown == 1e6`: [3](#0-2) 

4. The breach check becomes `metric < (hwm * 0) / E6 == 0`, which is always `false` for `uint256`: [4](#0-3) 

5. The pool admin is a **semi-trusted** role constrained by caps — the scope pivot explicitly names "pool admin exceeds caps" as a valid admin-boundary break. The `onlyPoolAdmin` modifier resolves to `IMetricOmmPoolFactory(FACTORY).poolAdmin(pool_)`: [5](#0-4) 

6. The propose→execute flow with timelock is the only path to change `drawdownE6`, and `_validateDrawdown` is the sole cap guard: [6](#0-5) 

---

### Title
Off-by-one in `_validateDrawdown` allows pool admin to set `drawdownE6 = 1e6`, silently disabling stop-loss for all LPs — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`_validateDrawdown` uses a strict `>` comparison, permitting `drawdownE6 == 1e6` (100%). When this value is stored and later read in `_afterSwapOracleStopLoss`, `floorMultiplier` becomes `0`. The breach predicate in `_applyWatermark` reduces to `metric < 0`, which is structurally impossible for `uint256`. The stop-loss is permanently silenced for every bin in the pool after the timelock elapses, without any on-chain signal.

---

### Finding Description

`_validateDrawdown` at line 306 rejects only values **strictly greater than** `E6`:

```solidity
if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
```

`E6 == 1e6` therefore passes. After `executeOracleStopLossDrawdown` stores it:

```solidity
oracleStopLossConfig[pool_].drawdownE6 = drawdown;   // == 1e6
```

In `_afterSwapOracleStopLoss`:

```solidity
uint256 drawdown = cfg.drawdownE6;          // 1e6
if (drawdown == 0) return;                  // does NOT fire
uint256 floorMultiplier = E6 - drawdown;    // 1e6 - 1e6 = 0
```

In `_applyWatermark`:

```solidity
breached = metric < (hwm * floorMultiplier) / E6;
//       = metric < (hwm * 0) / 1e6
//       = metric < 0          ← always false for uint256
```

`OracleStopLossTriggered` is never emitted regardless of how much value is drained from the bin. The stop-loss hook runs to completion on every swap but never reverts.

---

### Impact Explanation

LPs in any pool using `OracleValueStopLossExtension` lose their stop-loss protection entirely. A pool admin who sets `drawdownE6 = 1e6` after the timelock can then allow (or execute) value-draining swaps — e.g., a large directional swap that moves the oracle mid far from the bin's fair value — without the extension ever blocking them. LP principal is at risk of full drain with no on-chain circuit breaker.

This satisfies the **Admin-boundary break** impact gate: the pool admin exceeds the intended drawdown cap (which should be `< 100%`) through a validation off-by-one, disabling a core LP protection mechanism.

---

### Likelihood Explanation

The pool admin is a semi-trusted role constrained by caps and timelocks. The timelock gives LPs a window to exit, but LPs who do not actively monitor pending schedule changes are silently exposed after the timelock elapses. The action requires only two pool-admin transactions (`proposeOracleStopLossDrawdown(pool, 1e6)` then `executeOracleStopLossDrawdown(pool)`) and no privileged factory-owner or oracle-admin access.

---

### Recommendation

Change the validation to reject `drawdownE6 == E6` as well:

```solidity
// Before (allows 100% drawdown):
if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);

// After (rejects 100% drawdown):
if (drawdownE6 >= E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
```

Alternatively, add a symmetric early-exit for `drawdown == E6` alongside the existing `drawdown == 0` guard, but fixing the validation is cleaner and prevents the invalid value from ever being stored.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import "forge-std/Test.sol";
import {OracleValueStopLossExtension} from
    "metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol";
// ... (standard test harness setup as in OracleValueStopLossSubExtension.t.sol)

function test_drawdownE6_1e6_disables_stop_loss() public {
    uint104 t0 = 1000;
    uint104 t1 = 1000;
    uint128 price = uint128(Q64);

    _storeBin(0, t0, t1, BIN_SHARES);

    // Step 1: admin sets drawdown to exactly 1e6 (passes _validateDrawdown due to > not >=)
    vm.startPrank(admin);
    extension.proposeOracleStopLossDrawdown(address(mockPool), 1e6);
    extension.executeOracleStopLossDrawdown(address(mockPool));  // timelock=0 in test setup
    vm.stopPrank();

    // Confirm stored value
    (uint32 dd,,,) = extension.oracleStopLossConfig(address(mockPool));
    assertEq(dd, 1e6);

    // Step 2: establish watermarks at current value
    _exposeStopLoss(0, 0, price, false);

    // Step 3: drain the bin (remove 80% of both reserves)
    _storeBin(0, 200, 200, BIN_SHARES);

    // Step 4: assert stop-loss does NOT trigger despite 80% value loss
    // (no revert expected — OracleStopLossTriggered is never emitted)
    _exposeStopLoss(0, 0, price, true);   // zeroForOne — should have triggered
    _exposeStopLoss(0, 0, price, false);  // oneForZero — should have triggered
    // Both pass silently: DRAWDOWN_CAP_PREVENTS_FULL_DRAIN invariant is broken
}
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L103-120)
```text
  function proposeOracleStopLossDrawdown(address pool_, uint256 newMaxDrawdownE6) external onlyPoolAdmin(pool_) {
    _validateDrawdown(newMaxDrawdownE6);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDrawdownE6 = uint32(newMaxDrawdownE6);
    sched.pendingDrawdownExecuteAfter = executeAfter;
    emit OracleStopLossDrawdownProposed(pool_, newMaxDrawdownE6, executeAfter);
  }

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L217-217)
```text
    if (drawdown == 0) return;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L234-234)
```text
    uint256 floorMultiplier = E6 - drawdown;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L305-307)
```text
  function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L328-335)
```text
  function _applyWatermark(uint256 metric, uint256 hwm, uint256 floorMultiplier)
    private
    pure
    returns (uint256 newHwm, bool breached)
  {
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
    return (hwm, breached);
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L31-35)
```text
  modifier onlyPoolAdmin(address pool_) {
    address poolAdmin = IMetricOmmPoolFactory(FACTORY).poolAdmin(pool_);
    if (msg.sender != poolAdmin) revert OnlyPoolAdmin(pool_, msg.sender, poolAdmin);
    _;
  }
```
