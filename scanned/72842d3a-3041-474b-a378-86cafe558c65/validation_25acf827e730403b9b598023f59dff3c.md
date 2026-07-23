### Title
Pool Admin Can Reduce `OracleValueStopLossExtension` Timelock to Zero, Bypassing LP Protection Window and Immediately Disabling the Stop-Loss Guard — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension` uses a per-pool timelock to give LPs a reaction window before the pool admin can change drawdown, decay, or watermark parameters. However, the timelock itself can be reduced to zero — either at initialization (no minimum is enforced) or via `proposeOracleStopLossTimelock` after waiting the current timelock period. Once the timelock is zero, `_afterTimelock` returns `block.timestamp`, `_requireElapsed` passes immediately, and the admin can propose and execute any parameter change in the same transaction with no LP reaction window.

---

### Finding Description

`_afterTimelock` computes the execution deadline as:

```solidity
// OracleValueStopLossExtension.sol L297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

When `timelock == 0`, this returns exactly `block.timestamp`. The elapsed check is:

```solidity
// OracleValueStopLossExtension.sol L301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
```

`block.timestamp < block.timestamp` is always `false`, so the check passes immediately. The `initialize` function accepts any `uint32 timelock` value including zero with no minimum validation:

```solidity
// OracleValueStopLossExtension.sol L56-62
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);   // validated
_validateDecay(decayPerSecondE8); // validated
// timelock: NO validation — zero is accepted silently
oracleStopLossConfig[pool] = PoolStopLossConfig({
    drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
});
```

Additionally, even a pool initialized with a non-zero timelock can have it reduced to zero via `proposeOracleStopLossTimelock(pool, 0)` followed by `executeOracleStopLossTimelock` after the current timelock elapses. Once zero, all subsequent propose+execute pairs for drawdown, decay, and watermarks execute atomically in the same transaction.

The protocol's own test confirms this behavior is reachable:

```solidity
// OracleValueStopLossSubExtension.t.sol L249-255
function test_decayTimelockZeroExecutesImmediately() public {
    vm.startPrank(admin);
    extension.proposeOracleStopLossDecay(address(mockPool), 58);
    extension.executeOracleStopLossDecay(address(mockPool)); // same block — passes
    vm.stopPrank();
}
```

With timelock at zero, the admin can immediately call:

```solidity
proposeOracleStopLossDrawdown(pool, E6);   // drawdown = 100%
executeOracleStopLossDrawdown(pool);        // executes in same tx
```

This sets `drawdownE6 = E6`, making `floorMultiplier = E6 - E6 = 0` in `_afterSwapOracleStopLoss`:

```solidity
// OracleValueStopLossExtension.sol L234
uint256 floorMultiplier = E6 - drawdown; // = 0
```

The breach check becomes `metric < (hwm * 0) / E6 = 0`, which is always false — the stop-loss never triggers regardless of value loss.

---

### Impact Explanation

LPs deposit into a pool with `OracleValueStopLossExtension` expecting that the stop-loss guard protects their principal from value drain. The timelock is the sole mechanism guaranteeing a reaction window before the admin can weaken or disable that guard. Once the timelock is zero (either from initialization or after a single timelock-period wait), the admin can atomically disable the stop-loss (`drawdown = E6`) in one transaction, leaving LP funds exposed to unlimited oracle-price-driven value loss with no protection and no exit window. Alternatively, setting `drawdown = 0` makes `floorMultiplier = E6`, causing the stop-loss to trigger on any metric decrease, blocking all swaps and trapping LP liquidity.

**Severity: Medium** — direct loss of LP principal protection; requires pool admin to act adversarially, but the timelock is the explicit mechanism designed to bound that trust.

---

### Likelihood Explanation

- A pool can be deployed with `timelock = 0` from day one, making the stop-loss protection illusory for all LPs who deposit.
- For pools with a non-zero timelock, the admin must wait one timelock period to reduce it to zero — a one-time cost that permanently removes the LP protection window.
- The pool admin role is semi-trusted (the NatDoc says "monitor at least as often as the timelock or **trust the pool admin**"), but the timelock is specifically the mechanism that bounds that trust. Reducing it to zero collapses the trust boundary entirely.

---

### Recommendation

1. **Enforce a minimum timelock at initialization**: Add `require(timelock >= MIN_TIMELOCK)` in `initialize`, where `MIN_TIMELOCK` is a protocol-level constant (e.g., 1 day).
2. **Enforce a minimum timelock on updates**: Add the same floor check in `executeOracleStopLossTimelock` before writing the new value.
3. **Prevent timelock reduction below the current value** (optional, stronger): Require `newTimelock >= currentTimelock` so the timelock can only increase, never decrease.

```solidity
// In initialize and executeOracleStopLossTimelock:
uint32 MIN_TIMELOCK = 1 days;
require(timelock >= MIN_TIMELOCK, "TimelockTooShort");
```

---

### Proof of Concept

```solidity
// 1. Pool is initialized with timelock = 0 (no validation prevents this)
extension.initialize(pool, abi.encode(uint32(500_000), uint32(58), uint32(0)));

// 2. LPs deposit, expecting stop-loss protection

// 3. Admin atomically disables stop-loss in one transaction (no waiting):
extension.proposeOracleStopLossDrawdown(pool, 1e6);  // drawdown = 100%
extension.executeOracleStopLossDrawdown(pool);        // executes immediately (timelock = 0)

// 4. _afterSwapOracleStopLoss now computes floorMultiplier = 1e6 - 1e6 = 0
//    breach check: metric < (hwm * 0) / 1e6 = 0 → always false
//    Stop-loss is permanently disabled; LP value can drain without limit.

// Alternatively, for a pool with existing timelock T:
// Step A: proposeOracleStopLossTimelock(pool, 0); wait T seconds; executeOracleStopLossTimelock(pool)
// Step B: Now timelock = 0; repeat step 3 above atomically.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L78-94)
```text
  function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    emit OracleStopLossTimelockProposed(pool_, newTimelock, executeAfter);
  }

  function executeOracleStopLossTimelock(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingTimelockExecuteAfter == 0) revert OracleStopLossNoPendingTimelock(pool_);
    _requireElapsed(sched.pendingTimelockExecuteAfter);
    uint32 timelock = sched.pendingTimelock;
    oracleStopLossConfig[pool_].timelock = timelock;
    (sched.pendingTimelock, sched.pendingTimelockExecuteAfter) = (0, 0);
    emit OracleStopLossTimelockSet(pool_, timelock);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L215-217)
```text
    PoolStopLossConfig memory cfg = oracleStopLossConfig[pool_];
    uint256 drawdown = cfg.drawdownE6;
    if (drawdown == 0) return;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L234-234)
```text
    uint256 floorMultiplier = E6 - drawdown;
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
