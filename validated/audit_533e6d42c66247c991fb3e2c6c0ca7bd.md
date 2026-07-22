### Title
`uint32` Truncation in `_afterTimelock` Lets Pool Admin Bypass the OracleValueStopLoss Timelock Immediately — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterTimelock` casts `block.timestamp + timelock` to `uint32`. When the stored `timelock` is large enough to push the sum past `type(uint32).max`, the result silently wraps to a small past timestamp. Every subsequent `_requireElapsed` check then passes immediately, letting the pool admin execute drawdown, decay, or watermark changes with zero delay — defeating the LP-protection guarantee the timelock was designed to enforce.

---

### Finding Description

`_afterTimelock` computes the deadline for every pending admin proposal:

```solidity
// metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

`block.timestamp` is `uint256`; `oracleStopLossConfig[pool_].timelock` is `uint32`. The addition is performed in `uint256` space (no revert), but the explicit `uint32(…)` cast silently truncates the result. In Solidity ≥ 0.8 explicit downcasts are **not** checked.

`uint32` max is 4,294,967,295 (~year 2106). Current `block.timestamp` ≈ 1,700,000,000. Any `timelock` value greater than ≈ 2,594,967,295 seconds (~82 years) causes the sum to exceed `uint32` max and wrap to a small value that is already in the past.

`_requireElapsed` compares `block.timestamp` (`uint256`) against the wrapped `uint32` value (implicitly widened to `uint256`):

```solidity
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
```

A wrapped `executeAfter` of, say, 5,032,704 is trivially less than `block.timestamp` ≈ 1,700,000,000, so the guard never fires.

There is **no validation** on the `timelock` value at initialization or at proposal time:

```solidity
// initialize — no bound on timelock
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);
_validateDecay(decayPerSecondE8);
// timelock accepted as-is
```

```solidity
// proposeOracleStopLossTimelock — no bound on newTimelock
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);   // uses CURRENT timelock
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    ...
}
```

---

### Impact Explanation

Once the admin has installed `timelock = type(uint32).max`, every subsequent proposal (`proposeOracleStopLossDrawdown`, `proposeOracleStopLossDecay`, `proposeOracleStopLossHighWatermarks`) produces a wrapped `executeAfter` that is already elapsed. The admin can then immediately execute:

- **Drawdown → 0**: disables the stop-loss entirely (`if (drawdown == 0) return;`), removing LP protection with no notice.
- **Drawdown → E6 (100%)**: every swap breaches the floor and reverts, permanently bricking the pool's swap path.
- **Decay → 0**: freezes watermarks at their current level; any future value dip permanently blocks the affected swap direction.
- **Watermarks → inflated values**: immediately triggers `OracleStopLossTriggered` on the next swap, halting trading.

LPs who deposited relying on the stop-loss timelock as a reaction window lose that protection entirely. Swap functionality can be made permanently unusable, trapping LP assets.

---

### Likelihood Explanation

The attack requires the pool admin to act maliciously, but the exploit path is straightforward and requires no external conditions:

1. Pool is deployed with `timelock = 0` (no validation prevents this).
2. Admin calls `proposeOracleStopLossTimelock(pool, type(uint32).max)`. Because the current timelock is 0, `executeAfter = uint32(block.timestamp + 0) = block.timestamp`, which passes `_requireElapsed` immediately.
3. Admin calls `executeOracleStopLossTimelock` in the same block.
4. `oracleStopLossConfig[pool_].timelock` is now `type(uint32).max`.
5. Admin proposes any parameter change; `_afterTimelock` wraps; admin executes immediately.

Even with a non-zero initial timelock the admin only needs to wait once (through the existing timelock) to install the overflowing value, after which all future proposals bypass the delay.

---

### Recommendation

Replace the bare `uint32` cast with a checked conversion that reverts on overflow, and add an explicit upper bound on acceptable timelock values:

```solidity
uint256 constant MAX_TIMELOCK = 365 days * 2; // or any reasonable cap

function _afterTimelock(address pool_) private view returns (uint32) {
    uint256 result = block.timestamp + oracleStopLossConfig[pool_].timelock;
    require(result <= type(uint32).max, TimelockOverflow());
    return uint32(result);
}
```

Also add validation in `initialize` and `proposeOracleStopLossTimelock`:

```solidity
require(timelock <= MAX_TIMELOCK, TimelockTooLarge());
```

---

### Proof of Concept

```
State:  pool deployed with timelock = 0, drawdownE6 = 50_000

Step 1: admin calls proposeOracleStopLossTimelock(pool, type(uint32).max)
        _afterTimelock → uint32(block.timestamp + 0) = block.timestamp  ← passes immediately
        pendingTimelockExecuteAfter = block.timestamp

Step 2: admin calls executeOracleStopLossTimelock(pool)
        _requireElapsed(block.timestamp) → block.timestamp < block.timestamp is false → passes
        oracleStopLossConfig[pool].timelock = type(uint32).max  (4_294_967_295)

Step 3: admin calls proposeOracleStopLossDrawdown(pool, 0)
        _afterTimelock → uint32(1_700_000_000 + 4_294_967_295)
                       = uint32(5_994_967_295)
                       = 5_994_967_295 mod 4_294_967_296
                       = 1_699_999_999   ← one second in the past
        pendingDrawdownExecuteAfter = 1_699_999_999

Step 4: admin calls executeOracleStopLossDrawdown(pool)
        _requireElapsed(1_699_999_999) → 1_700_000_000 < 1_699_999_999 is false → passes
        drawdownE6 = 0  ← stop-loss silently disabled, no LP notice given
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L46-67)
```text
  function initialize(address pool, bytes calldata data)
    external
    override(BaseMetricExtension, IOracleValueStopLossExtension)
    onlyFactory
    returns (bytes4)
  {
    if (oracleStopLossConfig[pool].initialized) {
      revert OracleStopLossAlreadyInitialized(pool);
    }

    (uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
    _validateDrawdown(drawdownE6);
    _validateDecay(decayPerSecondE8);

    oracleStopLossConfig[pool] = PoolStopLossConfig({
      drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
    });

    emit OracleStopLossDrawdownSet(pool, drawdownE6);
    emit OracleStopLossDecaySet(pool, decayPerSecondE8);
    emit OracleStopLossTimelockSet(pool, timelock);
    return IMetricOmmExtensions.initialize.selector;
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L215-217)
```text
    PoolStopLossConfig memory cfg = oracleStopLossConfig[pool_];
    uint256 drawdown = cfg.drawdownE6;
    if (drawdown == 0) return;
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
