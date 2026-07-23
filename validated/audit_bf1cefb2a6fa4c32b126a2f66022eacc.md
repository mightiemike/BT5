### Title
`OracleValueStopLossExtension._afterTimelock` uint32 Overflow Lets Pool Admin Silently Bypass LP Protection Timelock — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`_afterTimelock` truncates `block.timestamp + timelock` to `uint32` without overflow protection. When the pool admin sets `timelock = type(uint32).max`, the addition wraps to a **past** timestamp, making every subsequent propose-then-execute cycle for drawdown, decay, and watermark changes immediately executable in the same block — the LP protection window is silently eliminated.

---

### Finding Description

`_afterTimelock` computes the execution deadline for all timelocked parameter changes: [1](#0-0) 

```solidity
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

`block.timestamp` is `uint256`; the addition is performed in `uint256` space and then **truncated** to `uint32`. With `timelock = type(uint32).max = 4 294 967 295` and `block.timestamp ≈ 1 753 000 000` (July 2026):

```
1 753 000 000 + 4 294 967 295 = 6 047 967 295
uint32(6 047 967 295) = 6 047 967 295 mod 2^32 = 1 752 999 999
```

`1 752 999 999 < block.timestamp`, so `_requireElapsed` (`block.timestamp < executeAfter`) is immediately `false` — the check passes in the **same block** as the proposal. [2](#0-1) 

Neither `initialize` nor `proposeOracleStopLossTimelock` validates the timelock value: [3](#0-2) 

```solidity
// initialize — drawdown and decay are validated, timelock is not
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);
_validateDecay(decayPerSecondE8);
// timelock stored as-is, no cap
``` [4](#0-3) 

```solidity
// proposeOracleStopLossTimelock — no cap on newTimelock
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    ...
}
```

**Attack path (post-deployment, no malicious setup required):**

1. Pool is deployed with a legitimate timelock (e.g., 1 day).
2. Pool admin calls `proposeOracleStopLossTimelock(pool, type(uint32).max)`. The proposal's own `executeAfter = block.timestamp + 1 day` (uses the current 1-day timelock — this is visible to LPs).
3. After 1 day, admin calls `executeOracleStopLossTimelock` — `timelock` is now `type(uint32).max`.
4. Admin calls `proposeOracleStopLossDrawdown(pool, 0)`. `_afterTimelock` overflows → `executeAfter` is a **past** timestamp.
5. Admin immediately calls `executeOracleStopLossDrawdown` in the **same block** — drawdown set to 0, stop-loss disabled.
6. LPs had **zero** reaction window for step 5.

The deception is that `type(uint32).max` appears to be a 136-year timelock to LPs monitoring events, but it actually **eliminates** the timelock entirely for all subsequent parameter changes.

The same overflow applies to `proposeOracleStopLossDecay` and `proposeOracleStopLossHighWatermarks`, which also call `_afterTimelock`. [5](#0-4) [6](#0-5) [7](#0-6) 

---

### Impact Explanation

The `OracleValueStopLossExtension` is the primary on-chain mechanism protecting LP principal from value extraction. Its NatDoc explicitly states: *"Drawdown and decay changes are timelocked so LPs can react."* [8](#0-7) 

Disabling the stop-loss (`drawdownE6 = 0`) or setting `drawdownE6 = 1e6` (100% — triggers on any value change, bricking all swaps) without the LP protection window means LPs cannot exit before the change takes effect. This is a direct loss-of-principal risk: LP tokens remain in a pool whose stop-loss guard has been silently removed, exposing them to extraction attacks the extension was designed to block.

The `afterSwap` hook enforces the stop-loss on every swap: [9](#0-8) 

Removing it mid-pool-life without LP notice is a fund-impacting admin-boundary break.

---

### Likelihood Explanation

The pool admin is semi-trusted; the timelock is the explicit constraint on their power over LP funds. The overflow requires a deliberate action (`type(uint32).max`), but the code provides no guard. Any pool admin who understands the truncation can exploit it after waiting the initial timelock period. The `proposeOracleStopLossTimelock` event emitted in step 2 shows `type(uint32).max` as the proposed value — LPs monitoring events would see a 136-year timelock and consider themselves safe, not realizing the overflow effect.

---

### Recommendation

Add a maximum timelock cap in both `initialize` and `proposeOracleStopLossTimelock`:

```solidity
uint32 private constant MAX_TIMELOCK = 365 days; // 31 536 000 — fits in uint32, no overflow risk

// In initialize:
if (timelock > MAX_TIMELOCK) revert InvalidTimelock(timelock);

// In proposeOracleStopLossTimelock:
if (newTimelock > MAX_TIMELOCK) revert InvalidTimelock(newTimelock);
```

Alternatively, perform the addition in `uint256` in `_afterTimelock` and revert if the result exceeds `type(uint32).max` before casting.

---

### Proof of Concept

```solidity
function test_timelockOverflowBypassesProtection() public {
    // Pool deployed with 1-day timelock (legitimate setup)
    // Admin proposes type(uint32).max as new timelock
    vm.prank(admin);
    extension.proposeOracleStopLossTimelock(address(pool), type(uint32).max);

    // Wait 1 day — the proposal is now executable under the current 1-day timelock
    vm.warp(block.timestamp + 1 days);
    vm.prank(admin);
    extension.executeOracleStopLossTimelock(address(pool));
    // timelock is now type(uint32).max

    // Propose drawdown = 0 — _afterTimelock overflows to a past timestamp
    vm.prank(admin);
    extension.proposeOracleStopLossDrawdown(address(pool), 0);

    // Execute immediately in the SAME block — no LP protection window
    vm.prank(admin);
    extension.executeOracleStopLossDrawdown(address(pool)); // succeeds without waiting

    // Stop-loss is now disabled — LPs had zero reaction time
    (uint32 drawdown,,,) = extension.oracleStopLossConfig(address(pool));
    assertEq(drawdown, 0);
}
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L13-28)
```text
/// @title OracleValueStopLossExtension
/// @notice Tracks per-bin value per share in token0 and token1 terms at the oracle mid,
///         against decaying high watermarks. Drawdown and decay changes are timelocked so LPs
///         can react; monitor at least as often as the timelock or trust the pool admin.
/// @dev Value formulas (Q64.64 mid = token1 per token0), per-share in bin scaled units:
///
///      metricToken0 = t0*SCALE/shares + (t1 * 2^64 / mid) * SCALE / shares
///      metricToken1 = (t0 * mid / 2^64) * SCALE / shares + t1*SCALE/shares
///
///      A pure mid move pushes the metrics in opposite directions; a value leak pushes both down.
///        - metricToken0 breach (mid suspect-high) blocks zeroForOne == true  (token1 outflow)
///        - metricToken1 breach (mid suspect-low)  blocks zeroForOne == false (token0 outflow)
///        - both breached blocks both directions
///
///      Watermarks decay linearly at decayPerSecondE8 (lazy, per bin). Guarantee: value per
///      share at oracle marks cannot fall faster than drawdown (one-time) + decay * t (ongoing).
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L130-136)
```text
  function proposeOracleStopLossDecay(address pool_, uint256 newDecayPerSecondE8) external onlyPoolAdmin(pool_) {
    _validateDecay(newDecayPerSecondE8);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDecayPerSecondE8 = uint32(newDecayPerSecondE8);
    sched.pendingDecayExecuteAfter = executeAfter;
    emit OracleStopLossDecayProposed(pool_, newDecayPerSecondE8, executeAfter);
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L185-204)
```text
  function afterSwap(
    address,
    address,
    bool zeroForOne,
    int128,
    uint128,
    uint256 packedSlot0Initial,
    uint256 packedSlot0Final,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    int128,
    int128,
    uint256,
    bytes calldata
  ) external override returns (bytes4) {
    // Only the factory can initialize, so an initialized msg.sender is a legit pool — no onlyPool needed.
    _requireInitialized(msg.sender);
    _afterSwapOracleStopLoss(msg.sender, packedSlot0Initial, packedSlot0Final, bidPriceX64, askPriceX64, zeroForOne);
    return IMetricOmmExtensions.afterSwap.selector;
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
