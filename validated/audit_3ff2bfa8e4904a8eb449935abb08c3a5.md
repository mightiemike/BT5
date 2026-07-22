### Title
Unbounded Timelock in `OracleValueStopLossExtension` Permanently Locks All Stop-Loss Configuration — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension` applies its current `timelock` value as the mandatory wait before any configuration change (drawdown, decay, watermarks, and the timelock itself) can be executed. Unlike `drawdownE6` (capped at `1e6`) and `decayPerSecondE8` (capped at `1e8`), the timelock has **no upper-bound validation**. A pool admin who sets `timelock = type(uint32).max` (≈136 years) permanently prevents any future adjustment to the stop-loss extension. If the stop-loss subsequently triggers on a normal market move, swaps are irreversibly blocked with no admin escape path.

---

### Finding Description

Every propose-and-execute flow in `OracleValueStopLossExtension` calls `_afterTimelock`:

```solidity
// OracleValueStopLossExtension.sol line 297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

This is used by **all four** propose functions:

- `proposeOracleStopLossTimelock` (line 78–83)
- `proposeOracleStopLossDrawdown` (line 103–109)
- `proposeOracleStopLossDecay` (line 130–136)
- `proposeOracleStopLossHighWatermarks` (line 157–165)

The timelock setter itself has no cap:

```solidity
// line 78-83
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);   // uses CURRENT timelock
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    ...
}
```

Compare with the validated parameters:

```solidity
// line 305-310
function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
}
function _validateDecay(uint256 decayPerSecondE8) private pure {
    if (decayPerSecondE8 > E8) revert OracleStopLossDecayTooLarge(decayPerSecondE8);
}
// No analogous _validateTimelock exists.
```

**Attack sequence:**

1. Pool is deployed with `timelock = 0` (or any small value).
2. Admin calls `proposeOracleStopLossTimelock(pool, type(uint32).max)`. Because the current timelock is 0, `executeAfter = block.timestamp + 0 = block.timestamp`, so the proposal is immediately executable.
3. Admin calls `executeOracleStopLossTimelock(pool)`. The timelock is now `type(uint32).max` ≈ 136 years.
4. Any subsequent `proposeOracleStopLoss*` call sets `executeAfter = block.timestamp + 136 years`. No change can ever be executed.
5. A normal market move causes `_checkAndUpdateWatermarks` to emit `OracleStopLossTriggered`, reverting every `afterSwap` call and permanently blocking all swaps on the pool.

The stop-loss `afterSwap` hook is the only hook implemented; `beforeRemoveLiquidity` / `afterRemoveLiquidity` are not, so LP withdrawals remain open. However, the pool's swap functionality is permanently destroyed.

---

### Impact Explanation

Once the timelock is set to `type(uint32).max`, the admin cannot:
- Lower the timelock (requires waiting 136 years)
- Adjust watermarks to clear a triggered stop-loss (same wait)
- Change drawdown or decay

Any subsequent oracle-price move that crosses the drawdown floor causes `OracleStopLossTriggered` to revert every swap permanently. The pool becomes a one-way liquidity sink: LPs can exit but no trading is possible, destroying the pool's core utility and any fee revenue owed to LPs and the protocol.

---

### Likelihood Explanation

The trigger is a pool admin action. Pool admins are semi-trusted but the code explicitly notes they "must be trusted" and "act to optimize pool profitability." An accidental misconfiguration (e.g., passing `type(uint32).max` intending to set a large but finite value, or a unit confusion between seconds and days) is plausible. The absence of any cap — while `drawdownE6` and `decayPerSecondE8` both have explicit caps — makes this an inconsistent and surprising omission that increases the probability of an accidental trigger.

---

### Recommendation

Add an upper-bound validation in `proposeOracleStopLossTimelock` analogous to the existing caps on drawdown and decay:

```solidity
uint32 private constant MAX_TIMELOCK = 30 days; // or another reasonable bound

function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    if (newTimelock > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(newTimelock);
    ...
}
```

Also apply the same cap in `initialize` so a pool cannot be deployed with an already-unbounded timelock.

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_unboundedTimelockPermanentlyLocksExtension() public {
    // 1. Deploy pool with timelock = 0
    OracleValueStopLossExtension ext = new OracleValueStopLossExtension(address(factory));
    vm.prank(address(factory));
    ext.initialize(address(pool), abi.encode(uint32(100_000), uint32(58), uint32(0)));

    // 2. Admin sets timelock to max uint32 — executes immediately (current timelock = 0)
    vm.startPrank(admin);
    ext.proposeOracleStopLossTimelock(address(pool), type(uint32).max);
    ext.executeOracleStopLossTimelock(address(pool));   // succeeds: 0-second wait

    // 3. Admin tries to lower the timelock — executeAfter is now block.timestamp + 136 years
    ext.proposeOracleStopLossTimelock(address(pool), uint32(1 days));
    vm.expectRevert(IOracleValueStopLossExtension.OracleStopLossTimelockNotElapsed.selector);
    ext.executeOracleStopLossTimelock(address(pool));   // reverts

    // 4. Admin tries to reset watermarks after stop-loss triggers — same 136-year wait
    ext.proposeOracleStopLossHighWatermarks(address(pool), 0, 0, 0);
    vm.expectRevert(IOracleValueStopLossExtension.OracleStopLossTimelockNotElapsed.selector);
    ext.executeOracleStopLossHighWatermarks(address(pool));  // reverts
    vm.stopPrank();

    // 5. Any swap that crosses the drawdown floor now reverts permanently
    vm.expectRevert(IOracleValueStopLossExtension.OracleStopLossTriggered.selector);
    pool.swap(/* params that move price past drawdown */);
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L157-165)
```text
  function proposeOracleStopLossHighWatermarks(address pool_, int8 binIdx, uint104 newHwmToken0, uint104 newHwmToken1)
    external
    onlyPoolAdmin(pool_)
  {
    _requireInitialized(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    pendingHighWatermark[pool_] =
      PendingHighWatermarks({token0: newHwmToken0, token1: newHwmToken1, binIdx: binIdx, executeAfter: executeAfter});
    emit OracleStopLossHighWatermarkProposed(pool_, binIdx, newHwmToken0, newHwmToken1, executeAfter);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L258-285)
```text
  function _checkAndUpdateWatermarks(
    address pool_,
    int8 binIdx,
    uint256 metricT0,
    uint256 metricT1,
    uint256 floorMultiplier,
    uint256 decayRate,
    bool zeroForOne
  ) private {
    BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
    uint256 dt = block.timestamp - hwmS.lastDecayTs;

    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
    }

    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token0 = uint104(hwm0);
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token1 = uint104(hwm1);
    hwmS.lastDecayTs = uint32(block.timestamp);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L297-299)
```text
  function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L305-311)
```text
  function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
  }

  function _validateDecay(uint256 decayPerSecondE8) private pure {
    if (decayPerSecondE8 > E8) revert OracleStopLossDecayTooLarge(decayPerSecondE8);
  }
```
