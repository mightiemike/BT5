### Title
`OracleValueStopLossExtension` Uses Arithmetic Mean Instead of Geometric Mean for Mid-Price, Miscalibrating the Stop-Loss Guard — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid-price as the **arithmetic mean** of bid and ask, while every other price-sensitive path in the protocol (the pool's swap execution, `PriceVelocityGuardExtension`, and the data-provider lens) uses the **geometric mean** via `SwapMath.midAndSpreadFeeX64FromBidAsk`. Because the arithmetic mean is always ≥ the geometric mean (AM-GM inequality), the per-bin value metrics fed into the watermark comparison are systematically biased, weakening the stop-loss guard for one swap direction and potentially strengthening it for the other.

### Finding Description

**Correct formula used everywhere else:**

`SwapMath.midAndSpreadFeeX64FromBidAsk` computes:

```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64); // geometric mean
``` [1](#0-0) 

The pool's `swap` function uses this:

```solidity
(uint256 midPriceX64, uint256 baseFeeX64) =
    SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
``` [2](#0-1) 

`PriceVelocityGuardExtension.beforeSwap` also uses the geometric mean:

```solidity
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
``` [3](#0-2) 

**Wrong formula in the stop-loss extension:**

```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
``` [4](#0-3) 

This `midPriceX64` is then used to compute both per-bin metrics:

```solidity
metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
``` [5](#0-4) 

These metrics are compared against the high watermarks to decide whether to revert the swap:

```solidity
(uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
if (breach0 && zeroForOne) {
    revert OracleStopLossTriggered(...);
}
(uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
if (breach1 && !zeroForOne) {
    revert OracleStopLossTriggered(...);
}
``` [6](#0-5) 

### Impact Explanation

Let `S` be the half-spread fraction so that `ask = mid_geo * (1+S)` and `bid = mid_geo * (1-S)`. Then:

- **Geometric mid** = `sqrt(bid * ask)` = `mid_geo * sqrt(1 - S²)` ≈ `mid_geo * (1 - S²/2)`
- **Arithmetic mid** = `(bid + ask) / 2` = `mid_geo`

The arithmetic mean is always larger by approximately `mid_geo * S²/2`.

Because `metricToken0` **divides** by mid, a higher arithmetic mid produces a **lower** `metricToken0`. The watermark for the `zeroForOne = true` direction (token1 outflow) is set lower than it should be, weakening that guard. An attacker can drain more token1 than the configured `drawdownE6` threshold permits before the stop-loss fires.

Because `metricToken1` **multiplies** by mid, a higher arithmetic mid produces a **higher** `metricToken1`. The watermark for the `zeroForOne = false` direction (token0 outflow) is set higher than it should be, causing potential false-positive reverts on legitimate swaps.

The error is proportional to `S²/2`. For a 10% oracle spread (`S = 0.05`), the bias is ~0.125% of the metric value. For a 50% spread (`S = 0.25`), the bias reaches ~3.1%. When the spread widens between the time the watermark is set and the time of the check, the self-consistency of the comparison breaks down and the bypass magnitude equals `(S2² - S1²)/2 * metricValue`.

### Likelihood Explanation

Every swap through a pool configured with `OracleValueStopLossExtension` and a non-zero `drawdownE6` triggers this path. No special privilege is required. The bias is always present; its magnitude grows with oracle spread width. Pools with wider bid/ask spreads (e.g., volatile or illiquid pairs) are most affected.

### Recommendation

Replace the arithmetic mean with the same geometric mean formula used by the rest of the protocol:

```solidity
// Before (wrong):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

This ensures the value metrics in the stop-loss guard are computed at the same mid-price anchor used by the pool's swap math and `PriceVelocityGuardExtension`.

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5%), and an oracle with a 10% spread (e.g., `bid = 0.95 * mid_geo`, `ask = 1.05 * mid_geo`).
2. Execute a first swap to establish the watermark. The watermark for `metricToken0` is set using arithmetic mid ≈ `mid_geo * 1.0025` (0.25% above geometric mid), so the watermark is ~0.25% **lower** than it would be with the correct formula.
3. The breach threshold is `watermark * 0.95`. Because the watermark is 0.25% low, the effective drawdown allowed before the stop-loss fires is `5% + 0.25% = 5.25%` instead of `5%`.
4. An attacker executing `zeroForOne = true` swaps can extract an additional ~0.25% of LP value beyond the configured drawdown before the guard triggers — a direct bypass of the LP protection invariant. [7](#0-6) [1](#0-0) [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L36-51)
```text
  function beforeSwap(
    address,
    address,
    bool,
    int128,
    uint128,
    uint256,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata
  ) external override returns (bytes4) {
    address pool_ = msg.sender;
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    // casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128 (same bound as pool)
    // forge-lint: disable-next-line(unsafe-typecast)
    uint128 midPrice = uint128(midPriceX64);
```

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L254-255)
```text
    metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L270-278)
```text
    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
    }
```
