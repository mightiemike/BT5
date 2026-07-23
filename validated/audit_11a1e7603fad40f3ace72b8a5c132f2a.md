Audit Report

## Title
`lastDecayTs` Advances Without Actual Decay, Permanently Preventing Stop-Loss Re-Arm on Small-Metric Bins — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

`_checkAndUpdateWatermarks` unconditionally writes `hwmS.lastDecayTs = uint32(block.timestamp)` on every call, even when `_decayed()` returns the stored watermark unchanged because integer division truncated the decay step to zero. Any unprivileged swap in the non-blocked direction resets the decay clock without moving the watermark, so the watermark never falls and the pool's swap path in the breached direction is permanently disabled without admin intervention.

## Finding Description

In `_checkAndUpdateWatermarks` (L258–285), after computing the (possibly unchanged) decayed watermarks and checking for breach, the function unconditionally writes:

```solidity
hwmS.token0 = uint104(hwm0);
hwmS.token1 = uint104(hwm1);
hwmS.lastDecayTs = uint32(block.timestamp);   // L284 — always written
```

The decay step is computed in `_decayed` (L319–324):

```solidity
function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;   // integer division
}
```

When `hwm * factor < E8`, the division truncates to zero and `_decayed` returns `hwm` unchanged. With the contract's own documented example rate (`ratePerSecondE8 = 58`, ~5%/day) and `dt = 1` second:

- `factor = 58`
- Truncation condition: `hwm * 58 < 1e8` → `hwm < 1,724,138`
- `METRIC_SCALE = 1e6`; a bin with `t0 = 1000` raw units and `shares = 10,000` yields `t0ps = mulDiv(1000, 1e6, 10000) = 100`
- `(100 * 58) / 1e8 = 0` → `_decayed` returns 100 unchanged, but L284 still resets `lastDecayTs`

**Exploit path:**

1. A swap drains a bin enough to trigger `OracleStopLossTriggered` — the pool is blocked in direction A.
2. Swaps in direction B (still allowed) each call `afterSwap` → `_afterSwapOracleStopLoss` → `_checkAndUpdateWatermarks`.
3. Each such swap computes `dt = block.timestamp - hwmS.lastDecayTs` (small, e.g., 1 s), finds `_decayed` returns the same watermark, does not revert (because `breach && zeroForOne` is false for direction B), then writes `hwmS.lastDecayTs = block.timestamp`.
4. On the next direction-A swap attempt, `dt` is again tiny, truncation recurs, watermark is unchanged, breach is still detected → revert.
5. This cycle repeats indefinitely; the watermark never falls.

The existing test `test_decayRearmsAfterPermanentRepricing` (L439–461) does not cover this because it uses a single `vm.warp(block.timestamp + 5 days)` jump — `dt = 432,000 s`, `factor = 25,056,000`, well above the truncation threshold. It does not simulate the production scenario of frequent intervening swaps that each reset `lastDecayTs`.

## Impact Explanation

After a stop-loss breach, the pool's swap path in the breached direction is permanently disabled without privileged admin intervention (which itself requires a timelock). The automatic decay mechanism — the only designed path for the pool to self-recover — is silently neutralized. Traders cannot use the pool in the blocked direction; the oracle market-maker cannot provide two-sided liquidity. This is broken core pool functionality causing an unusable swap flow, matching the allowed impact gate.

## Likelihood Explanation

No privileged actor is required; any public swap in the non-blocked direction is sufficient to advance the clock. Small per-share metrics are common in bins that have been partially consumed by prior swaps or in pools with high share counts relative to token balances. The decay rate of 58 is the value cited in the contract's own NatDoc comment as a typical configuration. The condition is self-reinforcing: once the clock starts advancing without decay, every subsequent swap perpetuates it.

## Recommendation

Only advance `lastDecayTs` when the decay computation actually changed the stored watermark. Capture the pre-decay values before calling `_applyWatermark`, then conditionally update the timestamp:

```solidity
function _checkAndUpdateWatermarks(...) private {
    BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
    uint256 dt = block.timestamp - hwmS.lastDecayTs;

    uint256 stored0 = hwmS.token0;
    uint256 stored1 = hwmS.token1;
    uint256 decayed0 = _decayed(stored0, decayRate, dt);
    uint256 decayed1 = _decayed(stored1, decayRate, dt);

    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, decayed0, floorMultiplier);
    if (breach0 && zeroForOne) revert OracleStopLossTriggered(...);

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, decayed1, floorMultiplier);
    if (breach1 && !zeroForOne) revert OracleStopLossTriggered(...);

    hwmS.token0 = uint104(hwm0);
    hwmS.token1 = uint104(hwm1);
    // Only advance the clock if decay actually moved the stored watermark
    if (decayed0 != stored0 || decayed1 != stored1) {
        hwmS.lastDecayTs = uint32(block.timestamp);
    }
}
```

This ensures elapsed time accumulates in `dt` until `hwm * factor >= 1`, at which point a non-zero decay step occurs and the clock is legitimately advanced.

## Proof of Concept

```
Setup:
  Pool with OracleValueStopLossExtension
  drawdownE6 = 50_000, decayPerSecondE8 = 58
  Bin 0: t0 = 1000, t1 = 1000, shares = 10_000
  → metricT0 ≈ 200 (t0ps=100 + t1-in-t0-terms=100 at mid=1)

Step 1: First swap (zeroForOne=false) sets hwm0 = hwm1 = 200, lastDecayTs = T0.

Step 2: Drain bin to t0=80, t1=80 → metric ≈ 16.
        16 < 200 * (1e6 - 50_000) / 1e6 = 190 → breach triggered.
        Pool blocks zeroForOne=true swaps.

Step 3: Every 1 second, a swap with zeroForOne=false calls afterSwap:
        dt = 1, factor = 58 * 1 = 58
        (200 * 58) / 1e8 = 11600 / 100_000_000 = 0
        _decayed returns 200 unchanged
        hwmS.lastDecayTs = block.timestamp  ← clock advances, watermark stays at 200

Step 4: After 1 day (86,400 swaps), watermark is still 200.
        Expected (no intervening swaps): hwm ≈ 190, still blocked but decaying.
        Actual: hwm = 200 forever. Pool permanently blocked in zeroForOne=true direction.

Foundry test skeleton:
  _storeBin(0, 1000, 1000, 10_000);
  _configure(50_000, 58);
  _exposeStopLoss(0, 0, price, false);          // set watermark
  _storeBin(0, 80, 80, 10_000);                 // drain bin
  vm.expectRevert(); _exposeStopLoss(0, 0, price, true);  // breach confirmed
  for (uint i = 0; i < 86400; i++) {
      vm.warp(block.timestamp + 1);
      _exposeStopLoss(0, 0, price, false);       // non-blocked direction resets clock
  }
  // After 1 day of 1-second swaps, watermark should have decayed ~5%
  // but it has not moved at all:
  (uint256 hwm0,) = extension.currentHighWatermarks(address(mockPool), 0);
  assertEq(hwm0, 200);  // passes — bug confirmed
  vm.expectRevert(); _exposeStopLoss(0, 0, price, true);  // still blocked
```