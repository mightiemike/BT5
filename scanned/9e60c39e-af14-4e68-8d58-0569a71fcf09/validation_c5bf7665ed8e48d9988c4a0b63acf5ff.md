I have the full code. Let me trace through the exact logic carefully.

The code is fully readable. Let me trace the exact arithmetic to verify the claim.

**`_metrics` with a 2x oracle spike (mid doubles):**
- `metricT0 = t0ps + (t1 * Q64 / (2·mid)) · SCALE/shares` → t1 component halved → **metricT0 DECREASES**
- `metricT1 = (t0 · (2·mid) / Q64) · SCALE/shares + t1ps` → t0 component doubled → **metricT1 INCREASES**

**`_applyWatermark` during spike:**
- `hwm0`: metricT0 < old hwm0 → watermark stays, no ratchet
- `hwm1`: metricT1 > old hwm1 → **watermark ratchets UP** to inflated value

**`_checkAndUpdateWatermarks` lines 281–284 — the unconditional write:**
```solidity
hwmS.token0 = uint104(hwm0);
hwmS.token1 = uint104(hwm1);   // ← inflated hwm1 is committed regardless of direction
hwmS.lastDecayTs = uint32(block.timestamp);
```

The watermark write is **not direction-gated**. After the spike swap (either direction), `hwm1` is permanently set to the inflated value.

**After oracle corrects:**
- `metricT1` returns to ~50% of `hwm1_inflated`
- Floor = `hwm1_inflated * floorMultiplier / E6` ≈ `0.95 · hwm1_inflated`
- `metricT1_normal ≈ 0.5 · hwm1_inflated` << floor → `breach1 = true`
- Any `!zeroForOne` swap hits line 276–278 and reverts with `OracleStopLossTriggered`

**Recovery path:** Admin must call `proposeOracleStopLossHighWatermarks` + wait timelock + `executeOracleStopLossHighWatermarks`, or wait for `_decayed` to bring `hwm1` down (at 58/E8 per second ≈ 5%/day, a 2x spike takes ~20 days to decay below the floor).

**Does this require trusted oracle admin or incorrect oracle data?** No. A transient price spike from Chainlink or Pyth during volatile market conditions is normal oracle behavior, not "incorrect off-chain data." No oracle admin action is needed — any public trader who swaps during the spike triggers the watermark inflation.

---

### Title
Oracle Price Spike Permanently Inflates High Watermark, Blocking Legitimate Swaps — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

### Summary
`_checkAndUpdateWatermarks` unconditionally commits the new watermark after every swap. When the oracle reports a transient price spike, `metricT1` is inflated and `hwm1` is ratcheted up permanently. After the oracle corrects, all `!zeroForOne` swaps revert with `OracleStopLossTriggered` because the true-price metric falls far below the inflated watermark floor.

### Finding Description
In `_checkAndUpdateWatermarks`, the watermark write at lines 281–284 is unconditional: [1](#0-0) 

`_applyWatermark` ratchets up whenever `metric >= hwm`: [2](#0-1) 

`_metrics` computes `metricT1` as the token0 balance converted to token1 terms via the oracle mid-price: [3](#0-2) 

When the oracle mid doubles, `metricT1` doubles, `hwm1` is ratcheted to 2× its prior value, and this inflated value is written to storage. When the oracle corrects, `metricT1` returns to its true value (~50% of `hwm1_inflated`), which is far below the drawdown floor (`hwm1 * floorMultiplier / E6` ≈ 95% of `hwm1_inflated`). Every subsequent `!zeroForOne` swap reverts: [4](#0-3) 

The `bidPriceX64` and `askPriceX64` fed to `afterSwap` come directly from the pool's price provider at swap time with no spike-filtering: [5](#0-4) 

### Impact Explanation
Swaps in the `!zeroForOne` direction are permanently blocked after a transient oracle spike. Recovery requires either: (a) waiting for `_decayed` to erode the inflated watermark (at 5%/day, a 2× spike takes ~20 days), or (b) admin intervention via `proposeOracleStopLossHighWatermarks` + timelock + `executeOracleStopLossHighWatermarks`. During this window, one swap direction is completely unusable — broken core pool functionality. [6](#0-5) 

### Likelihood Explanation
Any public trader who executes a swap during a natural oracle price spike (Chainlink/Pyth temporary high during volatile markets) triggers the inflation. No oracle admin action, no malicious pool setup, and no incorrect oracle data is required — only a real transient price movement that the oracle faithfully reports.

### Recommendation
Before ratcheting `hwm1` upward, verify that the new metric is consistent with the opposite metric not having dropped (i.e., both metrics should move together on genuine value increases, not in opposite directions as they do during a pure price move). Alternatively, only ratchet the watermark when the metric increase is corroborated by actual token balance growth, or apply a TWAP/median filter to the oracle price before using it for watermark updates.

### Proof of Concept
1. Deploy pool with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5%), `decayPerSecondE8 = 0`.
2. Seed bin 0 with equal token0/token1 at oracle mid = `Q64` (1:1). Execute a swap; `hwm1` is set to baseline.
3. Set oracle to `2·Q64` (2× spike). Execute any swap (either direction). `metricT1` doubles; `hwm1` is ratcheted to 2× baseline.
4. Restore oracle to `Q64`. Attempt a `!zeroForOne` swap.
5. Assert revert with `OracleStopLossTriggered`: `metricT1_true ≈ 0.5 · hwm1_inflated < 0.95 · hwm1_inflated`.

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L255-255)
```text
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L275-278)
```text
    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L318-324)
```text
  /// @dev Linear decay; floors at 0 (ratchet restores from the live metric on next touch).
  function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L333-335)
```text
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
    return (hwm, breached);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L228-228)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();
```
