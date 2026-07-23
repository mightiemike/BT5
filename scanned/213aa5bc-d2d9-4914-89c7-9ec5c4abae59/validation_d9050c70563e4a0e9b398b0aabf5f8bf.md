### Title
Stop-Loss Guard Bypassed at Exact Drawdown Floor Due to Strict Inequality — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary
`OracleValueStopLossExtension._applyWatermark` uses a strict `<` comparison to detect a drawdown breach. When the per-share metric equals **exactly** the configured floor (`hwm * floorMultiplier / E6`), the breach evaluates to `false`, the stop-loss does not revert, and the swap completes. This is the direct structural analog of Allora M-6: the wrong comparison operator at a guard boundary allows the threshold to be crossed without triggering the protective halt.

---

### Finding Description

`_applyWatermark` is the core predicate of the stop-loss: [1](#0-0) 

```solidity
function _applyWatermark(uint256 metric, uint256 hwm, uint256 floorMultiplier)
    private pure returns (uint256 newHwm, bool breached)
{
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;   // ← strict <
    return (hwm, breached);
}
```

`floorMultiplier = E6 - drawdownE6`, so the floor is `hwm × (1 − drawdown)`. The stop-loss is intended to block any swap that causes the per-share value to fall to or below that floor. With strict `<`, when `metric == floor` exactly:

- `breached = false`
- The function returns `(hwm, false)` — watermark unchanged, no breach signalled
- `_checkAndUpdateWatermarks` does **not** revert [2](#0-1) 

The swap completes. The watermark is written back as `hwm` (not updated to the new lower metric), so every subsequent swap still compares against the same high-water mark. If the metric stays at exactly the floor across multiple swaps, the stop-loss never fires.

The hook is wired as an `afterSwap` extension: [3](#0-2) 

A revert inside `afterSwap` rolls back the entire transaction including the swap state changes, so the stop-loss is the last line of defence for LP value. Failing to revert at the exact floor means the LP's position value has already fallen to the maximum permitted drawdown with no protective halt.

---

### Impact Explanation

The `OracleValueStopLossExtension` is the primary on-chain mechanism protecting LP principal from oracle-driven value erosion. When `metric == floor`, the LP has lost exactly `drawdownE6 / 1e6` of their per-share value in that bin. The stop-loss was supposed to revert that swap; instead it silently passes. The LP suffers a direct loss of principal equal to the full configured drawdown, with no revert and no event indicating a breach.

---

### Likelihood Explanation

The floor value `hwm * (E6 − drawdownE6) / E6` is a concrete integer. The per-share metric is a deterministic function of bin balances and the oracle mid price. An attacker can compute off-chain the exact `amountSpecified` that lands `metricT0` or `metricT1` on the floor integer, then submit that swap. The oracle price introduces timing uncertainty, but the attacker can monitor the mempool and submit when the oracle price is known. The attack is feasible for any pool whose stop-loss watermarks and drawdown are readable on-chain (all storage is public).

---

### Recommendation

Change the strict inequality to non-strict in `_applyWatermark`:

```diff
- breached = metric < (hwm * floorMultiplier) / E6;
+ breached = metric <= (hwm * floorMultiplier) / E6;
```

This ensures the stop-loss triggers when the metric reaches **or** falls below the floor, consistent with the drawdown guarantee.

---

### Proof of Concept

1. Pool configured with `drawdownE6 = 100_000` (10%), watermark `hwm0 = 1_000_000` for bin 0.
2. Floor = `1_000_000 × 900_000 / 1_000_000 = 900_000`.
3. Attacker computes off-chain the `zeroForOne` swap amount that sets `metricT0 = 900_000` exactly.
4. Inside `_applyWatermark`: `metric = 900_000`, `hwm = 1_000_000`, `floor = 900_000`.
5. `breached = (900_000 < 900_000) = false` — no revert.
6. `_checkAndUpdateWatermarks` writes `hwmS.token0 = 900_000` (the unchanged `hwm` return value is `1_000_000`, but `hwm0` returned is `1_000_000`; the storage write is `uint104(hwm0)` = `1_000_000`).
7. Swap settles. LP's token0-denominated value per share in bin 0 has fallen by 10% — the full configured drawdown — with no protective halt. [1](#0-0)

### Citations

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
