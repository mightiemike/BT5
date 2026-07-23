Audit Report

## Title
`OracleValueStopLossExtension` Uses Arithmetic Mean Instead of Protocol-Standard Geometric Mean for Mid-Price, Miscalibrating the Stop-Loss Guard - (File: `metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary
`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid-price as the arithmetic mean `(bid + ask) / 2` at line 218, while the protocol-standard helper `SwapMath.midAndSpreadFeeX64FromBidAsk` computes the geometric mean `sqrt(bid × ask)`, which is used by both `MetricOmmPool.swap` and `PriceVelocityGuardExtension.beforeSwap`. By AM-GM inequality the arithmetic mean is always ≥ the geometric mean for any non-zero spread, causing the stop-loss extension to systematically overestimate mid-price. This miscalibrates the per-bin value watermarks so that the configured `drawdownE6` floor is not enforced at the correct value, allowing LP principal to drain below the intended floor before the guard fires.

## Finding Description
In `_afterSwapOracleStopLoss`, the mid-price is computed as:

```solidity
// OracleValueStopLossExtension.sol line 218
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
```

The protocol-standard helper in `SwapMath.sol` (lines 65–72) computes the geometric mean:

```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
```

`MetricOmmPool.swap` (lines 242–243) and `PriceVelocityGuardExtension.beforeSwap` (line 48) both call `SwapMath.midAndSpreadFeeX64FromBidAsk`, so the price used for swap settlement is always the geometric mean. The stop-loss extension uses a different, higher price.

The two per-bin metrics computed in `_metrics` (lines 246–256) are:
- `metricToken0 = t0·SCALE/shares + (t1·Q64/mid)·SCALE/shares` — the `t1/mid` term shrinks when `mid` is overestimated, so `metricToken0` is **underestimated**.
- `metricToken1 = (t0·mid/Q64)·SCALE/shares + t1·SCALE/shares` — the `t0·mid` term grows when `mid` is overestimated, so `metricToken1` is **overestimated**.

`_checkAndUpdateWatermarks` (lines 258–285) ratchets the high-watermark up to the current metric on each swap. Because `metricToken0` is underestimated, the watermark for token0 is anchored below the true per-share value. The drawdown floor `hwm · (1 − drawdownE6/1e6)` computed in `_applyWatermark` (lines 328–336) is therefore also below the intended floor. A swap that drains token0 value past the configured floor will not be reverted until the metric falls below the already-too-low floor, meaning LPs absorb extra loss before the guard fires. No existing guard compensates for this discrepancy; the arithmetic mean is hardcoded and the geometric-mean helper is never called in this file.

## Impact Explanation
The stop-loss extension is the primary on-chain mechanism protecting LP principal from oracle-price-driven value leakage. For `metricToken0`, the high-watermark is anchored below the true per-share value, so the drawdown floor is below the intended floor. A swap that drains token0 value past the configured floor will not be reverted until the metric falls below the (already-too-low) floor, meaning LPs absorb extra loss equal to the calibration error before the guard fires. The miscalibration is proportional to the oracle spread and is permanent — it cannot be corrected without redeploying the extension. This constitutes a direct loss of LP principal above Sherlock thresholds for pools with meaningful oracle spreads (e.g., RWA feeds, illiquid pairs).

## Likelihood Explanation
Every pool that deploys `OracleValueStopLossExtension` with a non-zero oracle spread is affected on every swap that touches a monitored bin. No special attacker action is required; the miscalibration is structural and is triggered by ordinary public swaps. The trigger is fully unprivileged. Pools using wider-spread oracles experience proportionally larger miscalibration.

## Recommendation
Replace the arithmetic mean with the same geometric-mean helper used everywhere else in the protocol:

```solidity
// In _afterSwapOracleStopLoss, replace:
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// With:
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(
    uint256(bidPriceX64), uint256(askPriceX64)
);
```

This aligns the stop-loss metric computation with the price the pool actually uses for swap settlement, ensuring the configured `drawdownE6` floor is enforced at the correct value.

## Proof of Concept
1. Deploy a pool with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5%), and an oracle with a 1000 bps spread (bid = 0.95·mid, ask = 1.05·mid).
2. Add liquidity to bin 0 with equal token0/token1 value.
3. Trigger `afterSwap`. The extension computes `midPriceX64 = (bid + ask) / 2` (arithmetic mean ≈ mid), while the true geometric mid is `sqrt(0.95·mid × 1.05·mid) = mid·sqrt(0.9975) ≈ 0.99875·mid`.
4. `metricToken0` is computed with the overestimated mid, producing a value ≈ 0.125% lower than the true metric. The watermark is anchored at this lower value.
5. Drain token0 value by 5% (the configured floor). The stop-loss does not trigger because the floor is 0.125% lower than intended.
6. Drain an additional 0.125% before the stop-loss finally triggers — demonstrating that LP principal below the configured floor was not protected.