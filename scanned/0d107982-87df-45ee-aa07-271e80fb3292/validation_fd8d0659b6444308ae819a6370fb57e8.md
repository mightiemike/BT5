### Title
Stop-Loss Guard Fails Open at Exact Drawdown Floor Boundary — (`File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._applyWatermark` uses a strict `<` comparison to decide whether a bin metric has breached the drawdown floor. When the post-swap metric lands exactly on the floor (`metric == hwm * floorMultiplier / E6`), `breached` is set to `false` and the swap is allowed to settle. The stop-loss fails open at the one value that should be its hard boundary.

---

### Finding Description

`_applyWatermark` is the core decision function of the oracle stop-loss. It compares the current per-share bin metric against the decayed high-watermark and the configured drawdown floor:

```solidity
// OracleValueStopLossExtension.sol  line 328-336
function _applyWatermark(uint256 metric, uint256 hwm, uint256 floorMultiplier)
    private pure returns (uint256 newHwm, bool breached)
{
    if (metric >= hwm) return (metric, false);          // ratchet up — no breach
    breached = metric < (hwm * floorMultiplier) / E6;  // ← strict less-than
    return (hwm, breached);
}
```

`floorMultiplier = E6 - drawdownE6`, so the floor is `hwm * (1 - drawdown)`. The intended invariant is: *value per share cannot fall by more than `drawdownE6 / E6` from the watermark*. When `metric == floor`, the drawdown has been reached exactly — the invariant is at its limit and the stop-loss should fire. Because the comparison is `<` rather than `<=`, `breached` is `false` at that exact point, and the calling code in `_checkAndUpdateWatermarks` does not revert:

```solidity
// lines 270-278
(uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
if (breach0 && zeroForOne) {
    revert OracleStopLossTriggered(...);
}
```

The watermark is then written back unchanged (`hwm`, not `metric`), so the next swap faces the same comparison against the same `hwm`. A second swap that moves the metric one unit below the floor will finally trigger the revert — but the boundary swap itself settled without protection.

---

### Impact Explanation

The stop-loss is the primary on-chain mechanism preventing LP value from draining beyond the configured drawdown. A swap that lands the metric exactly on the floor is the worst-case trade the protection was designed to block. Allowing it to settle means LP principal leaks by exactly one drawdown unit beyond the configured cap. For pools with large bin balances and a tight drawdown (e.g., `drawdownE6 = 50_000` = 5%), the unprotected boundary trade can represent a material loss of LP funds. The watermark is not updated to the new lower metric, so the pool does not self-correct; the next swap that crosses the floor by even one wei will revert, but the boundary trade's loss is already committed.

---

### Likelihood Explanation

Any public swap can trigger this. A trader (or a searcher watching mempool) who can observe the current watermark and bin balances can craft a swap whose output moves the metric to exactly the floor. The `afterSwap` hook receives the final `packedSlot0Final` and the oracle mid, so the exact metric is computable off-chain before submission. No privileged access, no malicious setup, and no non-standard token behavior is required.

---

### Recommendation

Change the strict less-than to less-than-or-equal in `_applyWatermark`:

```solidity
// Before
breached = metric < (hwm * floorMultiplier) / E6;

// After
breached = metric <= (hwm * floorMultiplier) / E6;
```

This ensures the stop-loss fires when the metric reaches the floor, not only when it falls below it, matching the stated invariant that value per share *cannot fall faster than drawdown*.

---

### Proof of Concept

Setup:
- Pool with one bin, `t0 = 1000`, `t1 = 1000`, `shares = BIN_SHARES`, `midPriceX64 = Q64` (1:1).
- `drawdownE6 = 50_000` (5%), `decayPerSecondE8 = 0`.
- First swap (zeroForOne = false) establishes the watermark. `metricT0 = metricT1 = 1000 * METRIC_SCALE / BIN_SHARES`.
- Watermark `hwm0 = hwm1 = metric`.
- Floor = `hwm * 950_000 / 1_000_000 = 0.95 * hwm`.

Attack swap:
- Attacker crafts a `zeroForOne = true` swap that drains token1 from the bin until `metricT0` equals exactly `0.95 * hwm0`.
- `_applyWatermark(metricT0, hwm0, 950_000)`:
  - `metric < hwm` → true (enters breach check).
  - `breached = metric < (hwm * 950_000) / 1_000_000` → `metric < metric` → **false**.
- `breach0 && zeroForOne` → `false && true` → **no revert**.
- Swap settles. LP has lost exactly 5% of token0-denominated value — the maximum the stop-loss was supposed to prevent.

A subsequent swap with `metricT0` one unit below the floor would correctly revert, but the boundary trade's loss is already final. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L207-243)
```text
  function _afterSwapOracleStopLoss(
    address pool_,
    uint256 packedSlot0Initial,
    uint256 packedSlot0Final,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bool zeroForOne
  ) internal {
    PoolStopLossConfig memory cfg = oracleStopLossConfig[pool_];
    uint256 drawdown = cfg.drawdownE6;
    if (drawdown == 0) return;
    uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
    uint256 minShares = IMetricOmmPool(pool_).getImmutables().minimalMintableLiquidity;
    if (minShares == 0) minShares = 1;
    PoolSlot0 memory s0 = Slot0Library.unpack(packedSlot0Initial);
    PoolSlot0 memory s1 = Slot0Library.unpack(packedSlot0Final);
    int8 lo = s0.curBinIdx < s1.curBinIdx ? s0.curBinIdx : s1.curBinIdx;
    int8 hi = s0.curBinIdx > s1.curBinIdx ? s0.curBinIdx : s1.curBinIdx;
    // forge-lint: disable-next-line(unsafe-typecast)
    uint256 count = uint256(int256(hi) - int256(lo) + 1);
    int8[] memory binIdxs = new int8[](count);
    for (uint256 i = 0; i < count; i++) {
      // forge-lint: disable-next-line(unsafe-typecast)
      binIdxs[i] = int8(int256(lo) + int256(i));
    }
    bytes32[] memory states = PoolStateLibrary._multipleBinStates(pool_, binIdxs);
    bytes32[] memory shares = PoolStateLibrary._multipleBinTotalShares(pool_, binIdxs);
    uint256 floorMultiplier = E6 - drawdown;
    uint256 decayRate = cfg.decayPerSecondE8;
    for (uint256 i = 0; i < count; i++) {
      uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
      if (totalShares == 0) continue;
      (uint104 t0, uint104 t1,,,) = PoolStateLibrary._decodeBinState(states[i]);
      (uint256 metricT0, uint256 metricT1) = _metrics(t0, t1, totalShares, minShares, midPriceX64);
      _checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1, floorMultiplier, decayRate, zeroForOne);
    }
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
