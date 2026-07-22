### Title
Unbounded `timelock` in `OracleValueStopLossExtension` permanently freezes all guard reconfiguration, bricking swap protection or all swaps — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension.initialize()` validates `drawdownE6` and `decayPerSecondE8` against upper bounds but applies **no upper bound to `timelock`**. The same omission exists in `proposeOracleStopLossTimelock()`. A pool admin can set `timelock = type(uint32).max` (≈ 136 years) either at pool creation or immediately post-creation when the current timelock is zero. Once set, every subsequent parameter change — including reducing the timelock itself — requires waiting 136 years, permanently freezing the guard in whatever state it was in at that moment.

---

### Finding Description

`initialize()` decodes three parameters and validates only two of them:

```solidity
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);   // ✓ capped at E6
_validateDecay(decayPerSecondE8); // ✓ capped at E8
// timelock — no validation
``` [1](#0-0) 

Every subsequent admin action — `proposeOracleStopLossDrawdown`, `proposeOracleStopLossDecay`, `proposeOracleStopLossHighWatermarks`, and `proposeOracleStopLossTimelock` itself — computes its `executeAfter` via `_afterTimelock`, which adds the **current** stored timelock to `block.timestamp`:

```solidity
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [2](#0-1) 

`proposeOracleStopLossTimelock` also uses `_afterTimelock`, so the timelock is self-referential: reducing it requires waiting the current timelock first. [3](#0-2) 

**Post-initialization attack path (no malicious setup required):**

1. Pool is created with `timelock = 0` (immediate execution, a common default).
2. Pool admin calls `proposeOracleStopLossTimelock(pool, type(uint32).max)`.
3. `_afterTimelock` returns `block.timestamp + 0 = block.timestamp`; `_requireElapsed` passes immediately.
4. Admin calls `executeOracleStopLossTimelock` in the same block.
5. `oracleStopLossConfig[pool_].timelock` is now `type(uint32).max` ≈ 136 years.
6. Every future `propose*` call sets `executeAfter = block.timestamp + 136 years`; no change can ever be executed.

`cancelOracleStopLossTimelock` only cancels a *pending* proposal; it cannot undo the already-committed timelock value. [4](#0-3) 

---

### Impact Explanation

Two fund-impacting outcomes depending on the frozen `drawdownE6` value:

**Scenario A — Swaps permanently bricked (`drawdownE6 = 1`):**  
`floorMultiplier = E6 - 1 = 999_999`. Any swap that causes even a 1-unit drop in per-share metric triggers `OracleStopLossTriggered`, reverting the swap. With `timelock = type(uint32).max`, the drawdown can never be raised. All swaps revert permanently; the pool is unusable for trading. LPs cannot exit via swaps and are stuck with illiquid positions.

**Scenario B — Stop-loss permanently disabled (`drawdownE6 = E6`):**  
`floorMultiplier = 0`, so `metric < 0` is never true for `uint256`; the guard never fires. With `timelock = type(uint32).max`, the drawdown can never be lowered. LPs have no protection against value extraction through repeated adversarial swaps that drain bin reserves below fair value. [5](#0-4) 

---

### Likelihood Explanation

The pool admin is a semi-trusted role. The trigger requires only two sequential admin calls when the current timelock is zero — a common initial configuration. Miscalculation (e.g., passing seconds instead of days, or passing `type(uint32).max` as a sentinel) is a realistic error. The `proposeOracleStopLossTimelock` function accepts any `uint32` with no validation, making the misconfiguration easy to commit and impossible to recover from. [6](#0-5) 

---

### Recommendation

Add an upper bound on `timelock` in both `initialize()` and `proposeOracleStopLossTimelock()`, analogous to the existing bounds on `drawdownE6` and `decayPerSecondE8`:

```solidity
uint32 internal constant MAX_TIMELOCK = 30 days;

function _validateTimelock(uint256 timelock) private pure {
    if (timelock > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(timelock);
}
```

Apply `_validateTimelock(timelock)` in `initialize()` alongside the existing validators, and apply it in `proposeOracleStopLossTimelock()` before storing `newTimelock`. [1](#0-0) 

---

### Proof of Concept

```solidity
// Pool created with timelock = 0
extension.initialize(pool, abi.encode(uint32(1), uint32(0), uint32(0)));
// drawdownE6 = 1 (any metric drop triggers stop-loss)
// timelock = 0 (immediate execution)

// Admin immediately locks the timelock to max
vm.prank(admin);
extension.proposeOracleStopLossTimelock(pool, type(uint32).max);
// executeAfter = block.timestamp + 0 = block.timestamp → passes _requireElapsed
vm.prank(admin);
extension.executeOracleStopLossTimelock(pool);
// oracleStopLossConfig[pool].timelock = type(uint32).max ≈ 136 years

// Now try to fix drawdown — will revert after 136 years
vm.prank(admin);
extension.proposeOracleStopLossDrawdown(pool, 500_000);
// executeAfter = block.timestamp + type(uint32).max → never executable

// All swaps now revert because drawdownE6 = 1 triggers on any metric drop
// Pool is permanently bricked for trading
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L96-101)
```text
  function cancelOracleStopLossTimelock(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingTimelockExecuteAfter == 0) revert OracleStopLossNoPendingTimelock(pool_);
    (sched.pendingTimelock, sched.pendingTimelockExecuteAfter) = (0, 0);
    emit OracleStopLossTimelockCancelled(pool_);
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
