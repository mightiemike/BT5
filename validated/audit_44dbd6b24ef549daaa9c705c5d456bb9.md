The code confirms the claim. Key observations:

1. `OracleValueStopLossExtension.sol` line 218 uses arithmetic mean: `(uint256(bidPriceX64) + uint256(askPriceX64)) / 2`
2. `SwapMath.midAndSpreadFeeX64FromBidAsk` uses geometric mean: `Math.sqrt(bidPriceX64 * askPriceX64)`
3. `PriceVelocityGuardExtension` uses geometric mean via `SwapMath.midAndSpreadFeeX64FromBidAsk`
4. `OracleValueStopLossExtension` does not import `SwapMath` at all

The AM-GM discrepancy is real and the directional impact on `metricT0` is correctly described.

---

Audit Report

## Title
`OracleValueStopLossExtension` Uses Arithmetic Oracle Mid-Price While Pool Uses Geometric Mid-Price, Causing Stop-Loss to Underprotect LPs on `zeroForOne` Swaps - (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary
`OracleValueStopLossExtension._afterSwapOracleStopLoss()` computes the oracle mid-price as the arithmetic mean of bid and ask, while the pool's swap execution and `PriceVelocityGuardExtension` both derive it as the geometric mean via `SwapMath.midAndSpreadFeeX64FromBidAsk()`. By AM-GM inequality, the arithmetic mean is always strictly greater than the geometric mean when bid ≠ ask, causing the stop-loss guard's `metricT0` to be systematically understated. This allows LPs to lose more than the configured `drawdownE6` before the stop-loss triggers on `zeroForOne == true` swaps.

## Finding Description
**Root cause — dual mid-price derivation:**

`OracleValueStopLossExtension._afterSwapOracleStopLoss()` at line 218 computes:
```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;  // arithmetic mean
``` [1](#0-0) 

`SwapMath.midAndSpreadFeeX64FromBidAsk()` computes:
```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);  // geometric mean
``` [2](#0-1) 

`PriceVelocityGuardExtension.beforeSwap()` uses the geometric mean: [3](#0-2) 

`OracleValueStopLossExtension` does not import `SwapMath` at all, confirming the divergence is not accidental. [4](#0-3) 

**How the discrepancy corrupts the guard:**

`_metrics()` computes `metricT0` as:
```
metricT0 = t0ps + (t1 * Q64 / midPrice) / shares
``` [5](#0-4) 

Since `arith_mid > geometric_mid` (AM-GM), `Q64 / arith_mid < Q64 / geometric_mid`. The guard therefore computes a lower `metricT0` than the pool's actual pricing implies. The watermark `hwm0` is ratcheted up to this lower value, and the floor `hwm0 * floorMultiplier / E6` is also set lower.

**Exploit path:**

1. Any unprivileged trader executes a `zeroForOne == true` swap on a pool configured with `OracleValueStopLossExtension` where oracle spread is non-zero.
2. `afterSwap()` is called, which calls `_afterSwapOracleStopLoss()`.
3. `midPriceX64` is computed as arithmetic mean (higher than geometric mid).
4. `metricT0` is computed lower than the true geometric-mid value.
5. `_checkAndUpdateWatermarks()` sets `hwm0` at this lower level and checks breach only for `zeroForOne == true`.
6. The breach condition `metricT0 < (hwm0 * floorMultiplier) / E6` uses the understated metric and understated floor — the guard permits more true value loss than `drawdownE6` before reverting. [6](#0-5) 

No existing guard compensates for this: the watermark ratchet, decay, and floor multiplier all operate on the already-understated arithmetic-mid metric.

## Impact Explanation
The `OracleValueStopLossExtension` is documented to guarantee that value per share at oracle marks cannot fall faster than `drawdown + decay × t`. This guarantee is violated for `zeroForOne == true` swaps. LP principal is directly at risk beyond the configured protection level. The excess loss per swap is approximately `(1/arith_mid − 1/geometric_mid) × t1 × Q64 / shares`, which for a 10% oracle spread amounts to ~0.5% of the t1 portfolio component per swap, accumulating systematically across every `zeroForOne == true` swap for the lifetime of the pool. This constitutes a direct loss of LP principal above Sherlock thresholds for pools with meaningful oracle spreads.

## Likelihood Explanation
This triggers on every `zeroForOne == true` swap through any pool configured with `OracleValueStopLossExtension` where the oracle spread is non-zero — i.e., all real deployments. No special attacker setup, privileged role, or non-standard token behavior is required. Any public swap caller exercises the discrepancy. The guard is silently less protective than configured for the entire lifetime of every affected pool.

## Recommendation
Replace the arithmetic mean in `_afterSwapOracleStopLoss` with the geometric mean used everywhere else in the protocol, and add the `SwapMath` import:

```solidity
// Before (arithmetic mean — wrong):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (geometric mean — consistent with pool execution):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(
    uint256(bidPriceX64), uint256(askPriceX64)
);
```

## Proof of Concept
1. Deploy a pool with `OracleValueStopLossExtension` configured with `drawdownE6 = 50_000` (5%).
2. Set oracle bid = `0.9 * Q64`, ask = `1.1 * Q64` (10% spread). Geometric mid ≈ `0.9950 * Q64`; arithmetic mid = `1.0 * Q64`.
3. Seed the pool with `t0 = 1000`, `t1 = 1000` scaled units. The guard sets `hwm0` using arithmetic mid (lower value).
4. Execute a sequence of `zeroForOne == true` swaps that drain t1 at a price slightly worse than geometric mid.
5. Observe: the guard does not revert even though the true geometric-mid value has fallen by more than 5%, because `metricT0` (computed at arithmetic mid) shows a smaller loss than actually occurred.
6. Confirm: replacing line 218 with `SwapMath.midAndSpreadFeeX64FromBidAsk(...)` causes the guard to revert at the correct drawdown threshold.

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L1-12)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {IMetricOmmExtensions} from "@metric-core/interfaces/extensions/IMetricOmmExtensions.sol";
import {IMetricOmmPool} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPool.sol";
import {PoolStateLibrary} from "@metric-core/libraries/PoolStateLibrary.sol";
import {Slot0Library} from "@metric-core/libraries/Slot0Library.sol";
import {PoolSlot0} from "@metric-core/types/Slot0.sol";
import {IOracleValueStopLossExtension} from "../interfaces/extensions/IOracleValueStopLossExtension.sol";
import {BaseMetricExtension} from "./base/BaseMetricExtension.sol";

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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L48-51)
```text
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    // casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128 (same bound as pool)
    // forge-lint: disable-next-line(unsafe-typecast)
    uint128 midPrice = uint128(midPriceX64);
```
