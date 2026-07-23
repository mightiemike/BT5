### Title
Clamped Metric Sentinel Corrupts High-Watermark, Permanently Blocking Swaps or Suppressing Stop-Loss — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`_clampMetric` silently caps an overflowing metric at `type(uint104).max`. Because `_applyWatermark` unconditionally ratchets the stored watermark up to whatever the current metric is, a single swap under extreme-but-legitimate oracle-price conditions writes `type(uint104).max` into `hwmS.token0` or `hwmS.token1`. Every subsequent swap with a normal metric then compares against that sentinel, producing a permanent false breach (swaps blocked) or, if the extreme price persists, a permanent suppression of real breaches (stop-loss blind).

---

### Finding Description

**Clamping path — `_metrics`** [1](#0-0) 

```
metricT0 = _clampMetric(t0ps + mulDiv(mulDiv(t1, Q64, midPriceX64), METRIC_SCALE, shares));
```

With `t1 = type(uint104).max`, `midPriceX64 = 1`, `shares = minShares = 1`:

```
mulDiv(t1, Q64, 1) = 2^104 * 2^64 = 2^168
mulDiv(2^168, 1e6, 1) ≈ 2^188   >>   type(uint104).max ≈ 2^104
```

`_clampMetric` returns `METRIC_MAX = type(uint104).max`. [2](#0-1) 

The existing test `test_dustShares_flooredByMinLiquidity_noRevert` explicitly confirms this outcome and asserts `hwm0 == type(uint104).max` — but does **not** test what happens on the next swap. [3](#0-2) 

**Watermark ratchet — `_applyWatermark`** [4](#0-3) 

```solidity
if (metric >= hwm) return (metric, false);   // ratchets up, no breach
```

When `metric = type(uint104).max` and the stored `hwm < type(uint104).max`, the condition is true and `newHwm = type(uint104).max` is returned with `breached = false`. No guard prevents the sentinel from being written.

**Storage write** [5](#0-4) 

```solidity
hwmS.token0 = uint104(hwm0);   // = type(uint104).max
```

**Two failure modes on subsequent swaps**

*Mode A — permanently triggered (swaps blocked):*  
The extreme price condition resolves. The next swap produces a normal `metricT0 = X` (e.g., 1000). `_applyWatermark(1000, type(uint104).max, 950_000)` evaluates:

```
breached = 1000 < (type(uint104).max * 950_000) / 1e6
         = 1000 < ~1.93e31   →  TRUE
```

`breach0 && zeroForOne` → `revert OracleStopLossTriggered`. Every `zeroForOne = true` swap is permanently blocked. [6](#0-5) 

*Mode B — permanently suppressed (stop-loss blind):*  
The extreme price persists. Every swap produces `metricT0 = type(uint104).max`. `_applyWatermark(type(uint104).max, type(uint104).max, ...)` → `metric >= hwm` → `breached = false`. Real value loss is invisible to the guard.

---

### Impact Explanation

- **Mode A**: Core swap functionality broken. All swaps in one direction revert with `OracleStopLossTriggered` regardless of actual pool health. LPs cannot rebalance; traders cannot trade. Recovery requires pool-admin action through `proposeOracleStopLossHighWatermarks` + `executeOracleStopLossHighWatermarks`, subject to the configured timelock. [7](#0-6) 

- **Mode B**: The stop-loss guard is silently disabled for the affected bin and direction. A real drawdown event goes undetected, allowing bad-price swaps to drain LP value without triggering the intended protection.

Both outcomes satisfy the contest's "broken core pool functionality" and "bad-price execution" impact gates.

---

### Likelihood Explanation

The conditions are reachable without any privileged action:

1. **Extreme oracle price** — `midPriceX64` is derived from `bidPriceX64 + askPriceX64` passed by the pool's oracle. For token pairs with a very large or very small price ratio (e.g., a high-value token0 paired with a micro-cap token1), `midPriceX64` can legitimately be 1 or near-zero in Q64.64 representation. [8](#0-7) 

2. **Large balance, few shares** — A bin with accumulated fees or a bin that has been partially drained of shares but retains token balances can have `totalShares` near `minShares` while `t0` or `t1` is large. The code itself floors shares at `minShares` (minimum 1). [9](#0-8) 

3. **Trigger** — Any unprivileged trader executing a swap through the public pool interface causes `afterSwap` to fire, which calls `_afterSwapOracleStopLoss` and writes the corrupted watermark. [10](#0-9) 

The code's own comment acknowledges the clamp is for "pathological oracle-price blowups" and asserts "normal bins with uint104 balances stay below this" — but this assertion fails for legitimate extreme-price pools. [2](#0-1) 

---

### Recommendation

In `_checkAndUpdateWatermarks`, detect when the returned `newHwm` equals `METRIC_MAX` and skip the watermark write (or treat it as a no-op for the ratchet). Alternatively, in `_applyWatermark`, refuse to ratchet up to a clamped sentinel:

```solidity
// proposed guard in _checkAndUpdateWatermarks
(uint256 hwm0, bool breach0) = _applyWatermark(...);
if (breach0 && zeroForOne) revert ...;
if (hwm0 < METRIC_MAX) {          // never persist the sentinel
    hwmS.token0 = uint104(hwm0);
}
```

Or, more robustly, propagate a `clamped` flag from `_clampMetric` and skip both the breach check and the watermark update when the metric is clamped, since the oracle price is outside the range where the stop-loss can make a meaningful comparison.

---

### Proof of Concept

```solidity
// Setup: bin with near-max balance, 1 share, extreme (low) oracle price
_storeBin(0, type(uint104).max, type(uint104).max, 1);
_configure(50_000, 0);   // 5% drawdown, no decay

// Swap 1: extreme price → metric clamped → watermark set to type(uint104).max
_exposeStopLoss(0, 0, 1 /* midPriceX64 = 1 */, false);
(uint256 hwm0,) = extension.currentHighWatermarks(address(pool), 0);
assertEq(hwm0, type(uint104).max);   // confirmed by existing test

// Swap 2: normal price, normal balances → false breach permanently blocks swaps
_storeBin(0, 1000, 1000, 1e18);
vm.expectRevert(OracleStopLossTriggered.selector);
_exposeStopLoss(0, 0, uint128(Q64), true);   // zeroForOne blocked forever
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L157-166)
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
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L185-203)
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
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L218-218)
```text
    uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L251-251)
```text
    uint256 shares = totalShares < minShares ? minShares : totalShares;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L254-255)
```text
    metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L270-273)
```text
    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L280-284)
```text
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token0 = uint104(hwm0);
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token1 = uint104(hwm1);
    hwmS.lastDecayTs = uint32(block.timestamp);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L313-316)
```text
  /// @dev Clamp pathological oracle-price blowups; normal bins with uint104 balances stay below this.
  function _clampMetric(uint256 metric) private pure returns (uint256) {
    return metric > METRIC_MAX ? METRIC_MAX : metric;
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

**File:** metric-periphery/test/extensions/OracleValueStopLossSubExtension.t.sol (L495-505)
```text
  function test_dustShares_flooredByMinLiquidity_noRevert() public {
    // Dust shares are floored at minimalMintableLiquidity; max uint104 balances clamp to uint104.max.
    _storeBin(0, type(uint104).max, type(uint104).max, 1);
    _configure(50_000, 0);

    _exposeStopLoss(0, 0, uint128(Q64), false);

    (uint256 hwm0, uint256 hwm1) = extension.currentHighWatermarks(address(mockPool), 0);
    assertEq(hwm0, type(uint104).max);
    assertEq(hwm1, type(uint104).max);
  }
```
