The code confirms the vulnerability. Let me verify the key facts:

1. **`_afterTimelock` at L297-299**: `uint32(block.timestamp + oracleStopLossConfig[pool_].timelock)` — no bounds on `timelock`
2. **`initialize` at L56-62**: calls `_validateDrawdown` and `_validateDecay` but no `_validateTimelock`
3. **`proposeOracleStopLossTimelock` at L78-84**: accepts any `uint32 newTimelock` without upper-bound check
4. **`_validateDrawdown`/`_validateDecay` at L305-311**: analogous guards exist for other params but not for `timelock`

The overflow math is correct: `uint32(1_753_000_000 + 4_294_967_295) = 1_752_999_999`, which is in the past, making `_requireElapsed` pass immediately.

---

Audit Report

## Title
Unbounded `timelock` in `_afterTimelock` Causes `uint32` Overflow, Allowing Pool Admin to Bypass LP-Protection Timelock Instantly - (File: `metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary
`OracleValueStopLossExtension` stores the per-pool timelock as `uint32` and computes proposal deadlines via `uint32(block.timestamp + timelock)`. When a pool admin sets `timelock = type(uint32).max`, the addition overflows on truncation to `uint32`, producing an `executeAfter` timestamp already in the past. Every subsequent `_requireElapsed` check passes immediately, giving the pool admin an instant bypass of the timelock for all future proposals — including setting `drawdownE6 = 0` to permanently disable the stop-loss guard.

## Finding Description
`_afterTimelock` is the sole function stamping every pending proposal with its earliest execution time:

```solidity
// L297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

`block.timestamp` is `uint256`; `timelock` is `uint32`. The addition is performed in `uint256` space and then truncated to `uint32`. With `timelock = type(uint32).max` (~4.29 × 10⁹) and `block.timestamp ≈ 1_753_000_000` (July 2026):

```
1_753_000_000 + 4_294_967_295 = 6_047_967_295
uint32(6_047_967_295) = 6_047_967_295 mod 4_294_967_296 = 1_752_999_999
```

The result is one second in the past. `_requireElapsed` then evaluates:

```solidity
// L301-303
if (block.timestamp < executeAfter) revert ...;
// 1_753_000_000 < 1_752_999_999 → false → no revert
```

Neither `initialize` (L56-62) nor `proposeOracleStopLossTimelock` (L78-84) validates an upper bound on the timelock value. Compare with `drawdownE6` and `decayPerSecondE8`, which both have explicit guards (`_validateDrawdown` at L305-307, `_validateDecay` at L309-311). No analogous `_validateTimelock` exists.

**Exploit path:**
1. Pool deployed with `timelock = 0` (no validation in `initialize`).
2. Admin calls `proposeOracleStopLossTimelock(pool, type(uint32).max)` — `_afterTimelock` returns `uint32(block.timestamp + 0) = block.timestamp`; `_requireElapsed` passes immediately.
3. Admin calls `executeOracleStopLossTimelock(pool)` — `timelock` is now `type(uint32).max`.
4. Admin calls `proposeOracleStopLossDrawdown(pool, 0)` — `_afterTimelock` overflows, `pendingDrawdownExecuteAfter` is set to a past timestamp.
5. Admin calls `executeOracleStopLossDrawdown(pool)` in the same transaction — `_requireElapsed` passes; `drawdownE6 = 0`.
6. `_afterSwapOracleStopLoss` now returns early at L217 (`if (drawdown == 0) return;`) on every swap — stop-loss permanently disabled.

## Impact Explanation
With the stop-loss guard disabled, the pool admin can collude with an oracle update to move the mid-price adversarially, draining LP token balances through swaps that would otherwise have been blocked by `OracleStopLossTriggered`. This is a direct loss of LP principal. The same overflow also allows instant execution of decay maximisation (`decayPerSecondE8 = E8`) and watermark zeroing, each independently disabling the guard. This satisfies the "admin-boundary break: pool admin bypasses timelocks" allowed impact and constitutes a High/Critical direct loss of user principal.

## Likelihood Explanation
The pool admin is a semi-trusted role; the protocol's own NatSpec states LPs must "monitor at least as often as the timelock." The timelock is the sole on-chain constraint on the admin's power over stop-loss parameters. A malicious or compromised admin can execute this attack in two steps (propose + execute) when the initial timelock is zero — a valid and unvalidated deployment configuration. No external oracle manipulation, special token behaviour, or privileged factory/oracle-admin role is required beyond the pool admin key.

## Recommendation
Add an upper-bound validation for `timelock` analogous to the existing `_validateDrawdown` and `_validateDecay` guards:

```solidity
uint32 private constant MAX_TIMELOCK = 365 days;

function _validateTimelock(uint256 timelock) private pure {
    if (timelock > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(timelock);
}
```

Call `_validateTimelock` in both `initialize()` and `proposeOracleStopLossTimelock()`. This prevents the `uint32` truncation overflow and ensures the timelock remains within a range where LPs can meaningfully react.

## Proof of Concept
```solidity
// Precondition: pool deployed with timelock = 0, drawdown = 500_000 (50%)

// Step 1: Propose timelock = type(uint32).max (executes immediately since current timelock = 0)
extension.proposeOracleStopLossTimelock(pool, type(uint32).max);
extension.executeOracleStopLossTimelock(pool);
// oracleStopLossConfig[pool].timelock = type(uint32).max

// Step 2: Propose drawdown = 0 — _afterTimelock overflows → past executeAfter
extension.proposeOracleStopLossDrawdown(pool, 0);

// Step 3: Execute immediately — _requireElapsed passes on past timestamp
extension.executeOracleStopLossDrawdown(pool);
// drawdownE6 = 0

// Step 4: All subsequent swaps skip stop-loss check
// _afterSwapOracleStopLoss: if (drawdown == 0) return;
// Admin can now drain LP funds via oracle-price manipulation with no on-chain stop-loss.
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L305-311)
```text
  function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
  }

  function _validateDecay(uint256 decayPerSecondE8) private pure {
    if (decayPerSecondE8 > E8) revert OracleStopLossDecayTooLarge(decayPerSecondE8);
  }
```
