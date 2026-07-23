### Title
`OracleValueStopLossExtension._checkAndUpdateWatermarks` blocks the recovery direction instead of the extraction direction, leaving LP funds unprotected — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

The `OracleValueStopLossExtension` is an `afterSwap` hook that computes per-share bin metrics and reverts if a configured drawdown floor is breached. The direction-aware blocking logic in `_checkAndUpdateWatermarks` is inverted: it blocks the swap direction that **adds** the depleted token back to the pool while allowing the swap direction that **extracts** it to proceed without restriction.

---

### Finding Description

The pool's `swap` function calls `_afterSwap` after settlement, which dispatches to `OracleValueStopLossExtension.afterSwap`, which calls `_afterSwapOracleStopLoss`, which calls `_checkAndUpdateWatermarks` for each touched bin. [1](#0-0) 

Inside `_checkAndUpdateWatermarks`:

```solidity
(uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
if (breach0 && zeroForOne) {
    revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
}

(uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
if (breach1 && !zeroForOne) {
    revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
}
``` [2](#0-1) 

The two metrics are defined as:

- `metricT0` = per-share bin value expressed in **token0 units**: `t0/shares + (t1/price)/shares`
- `metricT1` = per-share bin value expressed in **token1 units**: `(t0·price)/shares + t1/shares` [3](#0-2) 

The swap direction semantics (confirmed by pool code and the inline comment at line 206):

- `zeroForOne = true` → token0 **enters** the pool, token1 **leaves** the pool
- `zeroForOne = false` → token1 **enters** the pool, token0 **leaves** the pool [4](#0-3) 

**The inversion:**

| Metric breach | Token being extracted | Harmful direction | Code blocks |
|---|---|---|---|
| `breach0` (`metricT0` low) | token0 leaving pool | `zeroForOne = false` | `zeroForOne = true` ❌ |
| `breach1` (`metricT1` low) | token1 leaving pool | `zeroForOne = true` | `zeroForOne = false` ❌ |

When `metricT0` falls below the drawdown floor (token0 has been drained), the code reverts on `zeroForOne = true` — the direction that **adds** token0 back to the pool — while allowing `zeroForOne = false` — the direction that **continues extracting** token0 — to proceed without restriction.

The `afterSwap` hook fires after settlement, so the revert rolls back the entire transaction. An attacker executing `zeroForOne = false` swaps (extracting token0) will never trigger the stop-loss, because the check `breach0 && zeroForOne` evaluates to `true && false = false`. The stop-loss only fires when a benign user tries to add token0 back (`zeroForOne = true`), blocking recovery while extraction continues freely.

---

### Impact Explanation

LPs suffer direct, unbounded loss of principal. Once `metricT0` breaches the floor via repeated `!zeroForOne` swaps, the stop-loss:

1. **Fails to revert** any further `!zeroForOne` extraction (the harmful direction is never blocked).
2. **Reverts** any `zeroForOne` swap that would restore token0 value (the recovery direction is blocked).

The pool is left in a state where token0 can be drained to zero while the guard actively prevents recovery. The same applies symmetrically to token1 via `breach1`.

---

### Likelihood Explanation

The trigger is a public `swap` call. Any actor who can execute swaps on a pool with the `OracleValueStopLossExtension` configured (including pools with `allowAllSwappers = true` or where the attacker is allowlisted) can reach this path. No privileged access is required beyond the ability to swap. A stale or manipulated oracle price is the typical precondition, but even legitimate price movement that causes a drawdown will activate the inverted guard.

---

### Recommendation

Invert the direction conditions to block the swap direction that **extracts** the depleted token:

```diff
- if (breach0 && zeroForOne) {
+ if (breach0 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
  }

- if (breach1 && !zeroForOne) {
+ if (breach1 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
  }
``` [2](#0-1) 

---

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension` configured: `drawdownE6 = 50_000` (5% drawdown floor), `decayPerSecondE8 = 0`.
2. Seed the pool with token0 and token1 liquidity. The first `afterSwap` call sets watermarks for both metrics.
3. Execute a series of `zeroForOne = false` swaps (token1 in, token0 out) until `metricT0` falls below `hwm0 * 0.95`.
4. Observe: each `afterSwap` call evaluates `breach0 && zeroForOne` = `true && false` = `false` → **no revert**. All extraction swaps succeed.
5. Now attempt a `zeroForOne = true` swap (token0 in, token1 out) to restore the pool.
6. Observe: `afterSwap` evaluates `breach0 && zeroForOne` = `true && true` = `true` → **reverts with `OracleStopLossTriggered`**. Recovery is blocked.
7. Repeat step 3 indefinitely — token0 drains to zero while the stop-loss never fires on the extraction direction. [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L206-243)
```text
  /// @dev `zeroForOne` forwarded from the swap params (true = token0 in, token1 out of the pool).
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

**File:** metric-core/contracts/MetricOmmPool.sol (L250-278)
```text
    if (zeroForOne) {
      if (amount1Delta < 0) {
        // casting to uint256 is safe because amount1Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken1(recipient, uint256(-amount1Delta));
      }

      uint256 balance0Before = balance0();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
    } else {
      if (amount0Delta < 0) {
        // casting to uint256 is safe because amount0Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken0(recipient, uint256(-amount0Delta));
      }

      uint256 balance1Before = balance1();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount1Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount1Delta > 0 && balance1Before + uint256(amount1Delta) > balance1()) {
        revert IncorrectDelta();
      }
    }
```
