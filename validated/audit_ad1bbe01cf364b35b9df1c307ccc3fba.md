Audit Report

## Title
False `OracleStopLossTriggered` DoS via `_metrics` `minShares` Floor When `binTotalShares < minimalMintableLiquidity` — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

`_metrics` floors the share denominator at `minShares` when `totalShares < minShares`, artificially deflating per-share metrics relative to the previously recorded high-watermark. Because `removeLiquidity` only enforces `minimalMintableLiquidity` on the individual position's remaining shares (not the bin aggregate), a full LP exit can leave `binTotalShares` in `(0, minShares)`. The next swap in the affected direction then triggers a false `OracleStopLossTriggered` revert, and because the watermark is not updated on breach, every subsequent swap in that direction also reverts permanently until recovery.

## Finding Description

**Root cause — the floor in `_metrics`** ( [1](#0-0) ):
```solidity
uint256 shares = totalShares < minShares ? minShares : totalShares;
```
When `totalShares < minShares`, the denominator is inflated to `minShares`, making `metricT0` and `metricT1` lower than the true per-share value.

**Gap in `removeLiquidity`** — the `minimalMintableLiquidity` guard only fires when `newUserShares > 0` ( [2](#0-1) ):
```solidity
uint256 newUserShares = userShares - sharesToRemove;
if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
    revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
}
```
A full withdrawal (`newUserShares == 0`) passes unconditionally. With two LPs each holding shares ≥ `minShares` individually, one LP fully exiting can leave `binTotalShares` in `(0, minShares)`.

**Watermark not updated on breach** — the function reverts before writing the new watermark, so the old high-watermark persists and every subsequent swap in the affected direction also reverts ( [3](#0-2) ):
```solidity
(uint256 hwm0, bool breach0) = _applyWatermark(...);
if (breach0 && zeroForOne) {
    revert OracleStopLossTriggered(...); // watermark write never reached
}
...
hwmS.token0 = uint104(hwm0); // only reached if no breach
```

**Exploit path:**
1. Pool has `OracleValueStopLossExtension` with non-zero `drawdownE6`, `decayPerSecondE8 = 0`.
2. LP-A and LP-B each deposit shares ≥ `minShares` into the same bin; `binTotalShares ≥ 2 * minShares`.
3. A swap occurs; watermark is set to the true per-share metric (e.g., `hwm0 = 1_000_000`).
4. LP-A fully exits (`newUserShares = 0`, check passes); `binTotalShares` drops to `< minShares`.
5. Next legitimate swap calls `_afterSwapOracleStopLoss` → `_metrics` uses `minShares` as denominator → `metricT0` is artificially deflated below the drawdown threshold → `OracleStopLossTriggered` reverts.
6. Watermark remains at old high; every subsequent swap in that direction reverts.

The existing `if (totalShares == 0) continue;` guard at line 238 does not cover the `(0, minShares)` range. [4](#0-3) 

## Impact Explanation

Swaps touching any bin where `0 < binTotalShares < minimalMintableLiquidity` are permanently blocked in the direction that checks the breached metric. This is broken core pool functionality: the swap path is unusable. LP principal remains accessible via `removeLiquidity` (which does not invoke `afterSwap`), but the pool's primary trading function is DoS'd until new liquidity is added, the watermark decays to zero (only if `decayPerSecondE8 > 0`), or the pool admin resets the watermark (timelocked, not immediate). This matches the allowed impact: **broken core pool functionality causing unusable swap flows**.

## Likelihood Explanation

Any pool with `OracleValueStopLossExtension` and non-zero `drawdownE6` is affected. The condition is reachable through normal, unprivileged LP behavior — a full exit is a standard right. No attacker privilege is required. The scenario is especially likely in low-liquidity bins or during market stress when LPs withdraw. With `decayPerSecondE8 = 0` (a valid configuration), the DoS is permanent without admin intervention.

## Recommendation

Extend the existing zero-shares guard to also skip bins below `minShares`, consistent with the intent of the floor:

```solidity
// Before (line 238):
if (totalShares == 0) continue;

// After:
if (totalShares < minShares) continue;
```

This treats sub-floor bins as having no meaningful metric (analogous to the empty-bin skip already present), avoiding the false deflation. Alternatively, the `minShares` floor in `_metrics` could be removed entirely and replaced with a revert or skip when `totalShares < minShares`, but the guard extension is the minimal, least-invasive fix.

## Proof of Concept

```
Setup:
  minimalMintableLiquidity (minShares) = 1000
  drawdownE6 = 100_000 (10%)
  decayPerSecondE8 = 0

Step 1: LP-A adds 600 shares to bin 0; LP-B adds 600 shares to bin 0.
        binTotalShares[0] = 1200, t0 = 1200 (proportional).

Step 2: Swap occurs.
        _metrics(t0=1200, totalShares=1200, minShares=1000)
        → shares = 1200 (no floor), metricT0 = 1200*1e6/1200 = 1_000_000.
        Watermark hwm0 = 1_000_000.

Step 3: LP-A removes all 600 shares.
        newUserShares = 0 → MinimalLiquidity check skipped (condition: newUserShares > 0 is false).
        binTotalShares[0] = 600, t0 = 600 (proportional removal).

Step 4: Legitimate swap (zeroForOne=true) triggers afterSwap.
        _metrics(t0=600, totalShares=600, minShares=1000)
        → shares = 1000 (floor applied), metricT0 = 600*1e6/1000 = 600_000.
        threshold = 1_000_000 * (1_000_000 - 100_000) / 1_000_000 = 900_000.
        600_000 < 900_000 → OracleStopLossTriggered reverts.

True per-share value = 600*1e6/600 = 1_000_000 (unchanged — no value loss occurred).
Stop-loss is a false positive. Watermark remains at 1_000_000.

Step 5: All subsequent zeroForOne swaps also revert (watermark never updated).
        Pool swap functionality is permanently DoS'd in this direction.
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L237-238)
```text
      uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
      if (totalShares == 0) continue;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L251-251)
```text
    uint256 shares = totalShares < minShares ? minShares : totalShares;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L270-284)
```text
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
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L199-202)
```text
          uint256 newUserShares = userShares - sharesToRemove;
          if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }
```
