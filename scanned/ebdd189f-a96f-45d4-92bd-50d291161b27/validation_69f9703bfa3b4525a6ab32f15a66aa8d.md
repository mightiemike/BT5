### Title
`OracleValueStopLossExtension` Uses Arithmetic Mid-Price While Pool Uses Geometric Mid-Price, Causing Stop-Loss to Underprotect LPs on `zeroForOne` Swaps - (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss()` derives the oracle mid-price as the **arithmetic mean** of bid and ask, while every other price-sensitive path in the protocol (pool swap execution, `PriceVelocityGuardExtension`, `getSellAndBuyPrices`) derives it as the **geometric mean** via `SwapMath.midAndSpreadFeeX64FromBidAsk()`. Because the arithmetic mean is always strictly greater than the geometric mean when bid ≠ ask (AM-GM inequality), the stop-loss guard's `metricT0` value-per-share measurement is systematically understated relative to the pool's actual pricing. This causes the guard to detect a smaller value loss than actually occurred for `zeroForOne == true` swaps, allowing LPs to lose more than the configured `drawdownE6` before the stop-loss triggers.

---

### Finding Description

**Dual mid-price derivation — the exact analog of the IdleCDO dual-path bug:**

**Path 1 — Pool swap execution and `PriceVelocityGuardExtension` (geometric mean):**

`SwapMath.midAndSpreadFeeX64FromBidAsk` computes:
```
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64)   // geometric mean
``` [1](#0-0) 

This is the mid price used for all actual swap math in `MetricOmmPool.swap()`: [2](#0-1) 

And in `PriceVelocityGuardExtension.beforeSwap()`: [3](#0-2) 

**Path 2 — `OracleValueStopLossExtension` (arithmetic mean):**

```
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;  // arithmetic mean
``` [4](#0-3) 

This arithmetic mid is then fed into `_metrics()` to compute the per-share value used for watermark comparison: [5](#0-4) 

**How the discrepancy corrupts the guard for `zeroForOne == true` swaps:**

`metricT0` is the token0-denominated value per share:
```
metricT0 = t0/shares + (t1 * Q64 / midPrice) / shares
```

The t1-to-token0 conversion factor is `Q64 / midPrice`. Since `arith_mid > geometric_mid`, we have `Q64 / arith_mid < Q64 / geometric_mid`. The guard therefore sees a **lower** `metricT0` than the pool's actual pricing implies, and the watermark `hwm0` is also set at this lower level.

After a value-draining `zeroForOne == true` swap (token0 in, token1 out at an adverse price):
- True value loss at geometric mid: `|Δt1| * Q64 / geometric_mid / shares - Δt0/shares`
- Guard-measured loss at arithmetic mid: `|Δt1| * Q64 / arith_mid / shares - Δt0/shares`

Because `Q64/arith_mid < Q64/geometric_mid`, the guard measures a **smaller loss** than actually occurred. The guard's floor (`hwm0 * floorMultiplier`) is also set lower. The net effect is that the guard permits the pool to lose more than `drawdownE6` of its true (geometric-mid) value before reverting. [6](#0-5) 

The symmetric case (`metricT1` / `zeroForOne == false`) is over-sensitive (false positives), but the `metricT0` under-sensitivity is the fund-impacting direction.

---

### Impact Explanation

The `OracleValueStopLossExtension` is documented to guarantee: *"value per share at oracle marks cannot fall faster than drawdown (one-time) + decay × t."* Due to the arithmetic-vs-geometric mid discrepancy, this guarantee is violated for `zeroForOne == true` swaps. LPs can lose:

```
excess_loss ≈ (1/arith_mid − 1/geometric_mid) × t1_component × Q64 / shares
```

For a 1% oracle spread (bid=0.99, ask=1.01): excess ≈ 0.005% of the t1 portfolio component per swap.  
For a 10% oracle spread (bid=0.9, ask=1.1): excess ≈ 0.5% of the t1 portfolio component per swap.

The excess is bounded by the oracle spread but is systematic and accumulates across every `zeroForOne == true` swap. LP principal is directly at risk beyond the configured protection level.

---

### Likelihood Explanation

This triggers on every swap through a pool configured with `OracleValueStopLossExtension` where the oracle spread is non-zero (i.e., all real deployments). No special attacker setup is required — any public swap on such a pool exercises the discrepancy. The guard is silently less protective than configured for the entire lifetime of the pool.

---

### Recommendation

Replace the arithmetic mean in `_afterSwapOracleStopLoss` with the same geometric mean used everywhere else in the protocol:

```solidity
// Before (arithmetic mean — wrong):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (geometric mean — consistent with pool execution):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(
    uint256(bidPriceX64), uint256(askPriceX64)
);
```

This is the direct analog of the IdleCDO fix: consolidate to a single shared implementation so the guard measures value at the same price the pool actually uses for settlement.

---

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension` configured with `drawdownE6 = 50_000` (5%).
2. Set oracle bid = `0.9 * Q64`, ask = `1.1 * Q64` (10% spread).
   - Geometric mid ≈ `0.9950 * Q64`
   - Arithmetic mid = `1.0 * Q64`
3. Seed the pool with `t0 = 1000`, `t1 = 1000` scaled units. The guard sets `hwm0` using arithmetic mid.
4. Execute a sequence of `zeroForOne == true` swaps that drain t1 at a price slightly worse than geometric mid (simulating adverse selection).
5. Observe: the guard does not revert even though the true geometric-mid value has fallen by more than 5%, because the guard's `metricT0` (computed at arithmetic mid) shows a smaller loss than actually occurred.
6. Confirm: replacing line 218 with `SwapMath.midAndSpreadFeeX64FromBidAsk(...)` causes the guard to revert at the correct drawdown threshold.

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L64-72)
```text
  /// @notice Geometric mid price (Q64.64) and spread fee in Q64.64 from bid/ask oracle quotes.
  function midAndSpreadFeeX64FromBidAsk(uint256 bidPriceX64, uint256 askPriceX64)
    internal
    pure
    returns (uint256 midPriceX64, uint256 baseFeeX64)
  {
    midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
    baseFeeX64 = Math.mulDiv(askPriceX64, ONE_X64, midPriceX64, Math.Rounding.Ceil) - ONE_X64;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L242-243)
```text
    (uint256 midPriceX64, uint256 baseFeeX64) =
      SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L48-51)
```text
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    // casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128 (same bound as pool)
    // forge-lint: disable-next-line(unsafe-typecast)
    uint128 midPrice = uint128(midPriceX64);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L218-218)
```text
    uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L246-256)
```text
  function _metrics(uint104 t0, uint104 t1, uint256 totalShares, uint256 minShares, uint256 midPriceX64)
    private
    pure
    returns (uint256 metricT0, uint256 metricT1)
  {
    uint256 shares = totalShares < minShares ? minShares : totalShares;
    uint256 t0ps = Math.mulDiv(uint256(t0), METRIC_SCALE, shares);
    uint256 t1ps = Math.mulDiv(uint256(t1), METRIC_SCALE, shares);
    metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
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
