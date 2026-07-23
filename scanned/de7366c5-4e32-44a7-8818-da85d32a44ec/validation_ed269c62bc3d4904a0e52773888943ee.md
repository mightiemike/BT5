### Title
Zero Timelock Accepted at Initialization Allows Immediate Bypass of LP-Protection Stop-Loss Guard — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension.initialize` decodes a `timelock` parameter from caller-supplied `data` but applies **no minimum-value validation**. When `timelock = 0`, the pool admin can propose and execute any drawdown, decay, or watermark change in the same block, completely eliminating the LP reaction window the timelock is designed to provide. Setting `drawdownE6 = 1e6` (100 %) in this way silently disables the stop-loss guard for all future swaps.

---

### Finding Description

`initialize` validates `drawdownE6` and `decayPerSecondE8` but leaves `timelock` unchecked:

```solidity
// OracleValueStopLossExtension.sol L56-62
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) =
    abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);   // ✓ checked
_validateDecay(decayPerSecondE8); // ✓ checked
// timelock — NOT checked; zero is silently accepted
oracleStopLossConfig[pool] = PoolStopLossConfig({
    drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8,
    timelock: timelock, initialized: true
});
``` [1](#0-0) 

Every propose-then-execute flow derives its deadline from `_afterTimelock`:

```solidity
// L297-298
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [2](#0-1) 

With `timelock = 0`, `executeAfter = block.timestamp`. The elapsed check is:

```solidity
// L301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert ...;
}
``` [3](#0-2) 

`block.timestamp < block.timestamp` is always `false`, so `_requireElapsed` never reverts. The pool admin can therefore call `proposeOracleStopLossDrawdown` and `executeOracleStopLossDrawdown` in the same block (or even the same multicall transaction).

The same gap exists in `proposeOracleStopLossTimelock`: there is no lower-bound check on `newTimelock`, so an admin can also reduce a previously non-zero timelock to zero after the original delay elapses. [4](#0-3) 

**Effect on the stop-loss guard.** Once `drawdownE6 = E6 = 1e6` is executed, `floorMultiplier = E6 − drawdown = 0`. Inside `_applyWatermark`:

```solidity
// L334
breached = metric < (hwm * floorMultiplier) / E6;
// → metric < 0  (uint256 comparison) → always false
``` [5](#0-4) 

`_checkAndUpdateWatermarks` never reverts, so `afterSwap` never blocks any swap direction regardless of how much value has been drained from LP bins. [6](#0-5) 

---

### Impact Explanation

The `OracleValueStopLossExtension` is the primary on-chain mechanism preventing LP principal from being drained through adversarial or oracle-manipulated swaps. Its own NatSpec states the timelock exists so "LPs can react." With `timelock = 0`, that window is zero seconds. The pool admin can silently disable the guard in a single block, after which every subsequent swap executes without the stop-loss check. LPs suffer direct loss of deposited token0 and token1 principal with no on-chain recourse.

---

### Likelihood Explanation

The factory calls `initialize` with admin-supplied `data`; no factory-level validation of the `timelock` field exists in the reviewed code. A pool creator who is also the pool admin (a common pattern for permissioned pools) can deliberately or accidentally pass `timelock = 0`. The same outcome is reachable post-deployment by reducing a non-zero timelock to zero via `proposeOracleStopLossTimelock(pool_, 0)` once the original delay elapses. Both paths require only pool-admin privilege, which is the semi-trusted role the timelock is specifically designed to constrain.

---

### Recommendation

Add a minimum-timelock guard in `initialize` and in `proposeOracleStopLossTimelock`:

```solidity
uint32 private constant MIN_TIMELOCK = 1 hours;

function _validateTimelock(uint32 timelock) private pure {
    if (timelock < MIN_TIMELOCK) revert OracleStopLossTimelockTooShort(timelock);
}
```

Apply `_validateTimelock(timelock)` alongside the existing `_validateDrawdown` / `_validateDecay` calls in `initialize`, and apply it to `newTimelock` inside `proposeOracleStopLossTimelock`.

---

### Proof of Concept

1. Factory deploys a pool and calls `OracleValueStopLossExtension.initialize(pool, abi.encode(500_000, 58, 0))` — `drawdownE6 = 50 %`, `decayPerSecondE8 = 58`, **`timelock = 0`**. No revert.

2. LPs deposit into the pool, trusting the stop-loss guard.

3. Pool admin calls `proposeOracleStopLossDrawdown(pool, 1_000_000)` (100 % drawdown). `executeAfter = block.timestamp + 0 = block.timestamp`.

4. In the same transaction, pool admin calls `executeOracleStopLossDrawdown(pool)`. `_requireElapsed(block.timestamp)` evaluates `block.timestamp < block.timestamp` → `false` → no revert. `drawdownE6` is set to `1_000_000`.

5. `floorMultiplier = 1e6 − 1e6 = 0`. All future `afterSwap` calls reach `_applyWatermark` with `floorMultiplier = 0`; `breached` is always `false`; the stop-loss never fires.

6. Swaps that drain LP bins below any prior watermark proceed without revert. LP principal is lost.

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L301-303)
```text
  function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(executeAfter, block.timestamp);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L328-336)
```text
  function _applyWatermark(uint256 metric, uint256 hwm, uint256 floorMultiplier)
    private
    pure
    returns (uint256 newHwm, bool breached)
  {
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
    return (hwm, breached);
  }
```
