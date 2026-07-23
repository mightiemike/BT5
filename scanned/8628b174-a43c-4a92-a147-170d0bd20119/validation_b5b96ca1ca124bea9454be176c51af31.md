### Title
Stop-Loss Guard Uses Arithmetic Mid Price Instead of Geometric Mid Price, Allowing LP Value to Drain Beyond Configured Drawdown — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension` computes the oracle mid price as the **arithmetic mean** of bid and ask, while the pool uses the **geometric mean**. When the oracle spread widens between swaps, the per-share value metrics are inflated relative to the watermark baseline, causing the stop-loss guard to trigger later than configured. LPs can lose more than the configured `drawdownE6` before the guard fires.

---

### Finding Description

In `_afterSwapOracleStopLoss`, the mid price is computed as:

```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
``` [1](#0-0) 

The pool itself uses the geometric mean via `SwapMath.midAndSpreadFeeX64FromBidAsk`:

```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
``` [2](#0-1) 

By the AM-GM inequality, the arithmetic mean is always ≥ the geometric mean. The per-share `metricT1` (token1 value) is:

```solidity
metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
``` [3](#0-2) 

Because `midPriceX64` (AM) is larger than the pool's actual mid (GM), `metricT1` is **inflated** relative to the pool's internal pricing. The watermark ratchets up to this inflated value. When the spread later widens further, the current metric is inflated even more, so the stop-loss floor (`hwm * floorMultiplier / E6`) is never breached even though the actual LP value has fallen past the configured drawdown.

The breach check is:

```solidity
breached = metric < (hwm * floorMultiplier) / E6;
``` [4](#0-3) 

The watermark was set using AM-mid at spread S₁; the current metric is computed using AM-mid at spread S₂ > S₁. The ratio AM/GM(S₂) / AM/GM(S₁) inflates the current metric relative to the floor, suppressing the breach signal.

**Concrete arithmetic:**

Let bid = 100, ask = 200 (100% total spread, AM/GM ≈ 1.061). Watermark was set when spread = 1% (AM/GM ≈ 1.00005) at value V:

- Watermark = V × 1.00005  
- Floor (5% drawdown) = V × 1.00005 × 0.95 ≈ V × 0.9500  
- After 10% actual value loss: V′ = 0.90 V  
- Current metric = 0.90 V × 1.061 ≈ 0.9549 V  
- 0.9549 V > 0.9500 V → **stop-loss does NOT trigger**

LPs have lost 10% of their value while the configured drawdown was 5%.

---

### Impact Explanation

The `OracleValueStopLossExtension` is documented to guarantee: *"value per share at oracle marks cannot fall faster than drawdown (one-time) + decay × t (ongoing)."* This invariant is broken. When the oracle spread widens between the watermark-setting swap and the draining swap, the actual LP loss before the guard fires can be roughly `drawdown × (AM/GM_current / AM/GM_baseline)`. For pools whose oracle spread can vary from 1% to 50–100% (e.g., Pyth confidence-interval-based oracles during volatility spikes), the effective protection can be half the configured drawdown. This is a direct loss of LP principal beyond the configured safety threshold.

---

### Likelihood Explanation

The trigger is any unprivileged `swap()` call. No special role or malicious setup is required. The discrepancy activates whenever the oracle spread at the time of a draining swap is wider than the spread at the time the watermark was last set. For pools using Pyth or other confidence-interval oracles, spread variation is a normal market condition, not an edge case.

---

### Recommendation

Replace the arithmetic mean with the geometric mean, consistent with the pool's internal pricing:

```solidity
// Before (incorrect):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

`SwapMath.midAndSpreadFeeX64FromBidAsk` already computes `Math.sqrt(bidPriceX64 * askPriceX64)` and is available in the import path already used by `PriceVelocityGuardExtension`. [5](#0-4) 

---

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5%).
2. Oracle: bid = 990, ask = 1010 (~2% spread). A swap occurs; watermark for bin 0 is set to `metricT1 = V × AM/GM(1%)`.
3. Oracle moves to bid = 100, ask = 200 (100% spread — e.g., Pyth confidence interval widens during a volatility event).
4. Attacker calls `swap(zeroForOne=false, ...)`, draining 10% of token0 from the pool.
5. `afterSwap` computes `midPriceX64 = (100 + 200)/2 = 150` (AM). `metricT1 = t0_new × 150/Q64 + t1_new`.
6. Because AM/GM(100% spread) ≈ 1.061 >> AM/GM(1%) ≈ 1.00005, the current metric is inflated relative to the watermark floor.
7. The breach check `metric < hwm × 0.95` evaluates false even though actual LP value has fallen 10%.
8. The swap succeeds; the stop-loss guard is silently bypassed; LPs lose twice the configured drawdown. [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L255-255)
```text
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L334-334)
```text
    breached = metric < (hwm * floorMultiplier) / E6;
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L70-71)
```text
    midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
    baseFeeX64 = Math.mulDiv(askPriceX64, ONE_X64, midPriceX64, Math.Rounding.Ceil) - ONE_X64;
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L48-48)
```text
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```
