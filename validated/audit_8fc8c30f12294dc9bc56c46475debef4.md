### Title
`OracleValueStopLossExtension` Uses Arithmetic Mid-Price Instead of Geometric Mid-Price, Inflating Per-Share Metrics and Allowing Stop-Loss Guard to Fail Open - (File: `metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid-price as the **arithmetic mean** of bid and ask, while every other component in the protocol (the pool's swap engine and `PriceVelocityGuardExtension`) uses the **geometric mean** via `SwapMath.midAndSpreadFeeX64FromBidAsk`. By AM-GM inequality the arithmetic mean is always strictly greater than the geometric mean when `bid ≠ ask`. This inflates the per-share token1 metric that the stop-loss guard compares against its watermark floor, causing the guard to see a healthier pool than actually exists and fail to revert swaps that should be blocked.

---

### Finding Description

In `_afterSwapOracleStopLoss`, the mid-price used to value token1 holdings is:

```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
``` [1](#0-0) 

This is the arithmetic mean. The pool's swap engine and `PriceVelocityGuardExtension` both use the geometric mean:

```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
``` [2](#0-1) 

`PriceVelocityGuardExtension.beforeSwap` correctly calls:

```solidity
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
``` [3](#0-2) 

The stop-loss extension then passes this inflated `midPriceX64` into `_metrics(t0, t1, totalShares, minShares, midPriceX64)` to compute the per-share token1 value in token0 units for each bin touched by the swap. [4](#0-3) 

Because `(bid + ask)/2 > sqrt(bid * ask)` whenever `bid < ask` (which the pool always enforces), the per-share token1 metric is always overstated relative to the price the pool actually uses for settlement. The guard then compares this inflated metric against `hwm1 * floorMultiplier / E6` and concludes the pool is above the drawdown floor when it may not be. [5](#0-4) 

---

### Impact Explanation

The stop-loss extension is the primary on-chain mechanism protecting LP principal from oracle-driven value leakage. When the guard uses an inflated mid-price, the per-share token1 metric is overstated by a factor proportional to the bid-ask spread. For a pool with a 1% spread (typical), the arithmetic mean exceeds the geometric mean by approximately `spread²/8 ≈ 0.0125%` per evaluation. For wider spreads (e.g., 5%), the overstatement reaches ~0.3%. This means the guard's effective drawdown floor is silently raised by that amount — the pool must lose *more* value than the configured `drawdownE6` before the guard triggers. LPs suffer direct loss of principal beyond the intended protection threshold on every swap that should have been blocked.

---

### Likelihood Explanation

The bug is triggered on every swap that touches a bin with a configured stop-loss watermark. No special setup is required beyond a pool having the `OracleValueStopLossExtension` active with a non-zero `drawdownE6`. Any public user executing a swap is the unprivileged trigger. The magnitude of the bypass scales with the oracle spread, which is always non-zero in a live pool.

---

### Recommendation

Replace the arithmetic mean with the geometric mean, consistent with the rest of the protocol:

```solidity
// Before (wrong):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(
    uint256(bidPriceX64),
    uint256(askPriceX64)
);
``` [1](#0-0) 

---

### Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5%), and a price provider returning `bid = 0.99e18` (Q64.64), `ask = 1.01e18` (Q64.64) — a 2% spread.
2. Set a high watermark for bin 0 at the current per-share values.
3. Execute swaps that drain exactly 5% of per-share token1 value at the **geometric** mid price `sqrt(0.99 * 1.01) ≈ 0.99995`.
4. Observe: the stop-loss guard computes mid as `(0.99 + 1.01)/2 = 1.00` (arithmetic), inflating `metricT1` by `~0.005%` relative to the geometric mid. The guard sees the metric as above the floor and does **not** revert.
5. The swap that should have been blocked by the 5% drawdown threshold executes, and LP funds leak beyond the configured protection boundary.

The exact overstatement is `(arithmetic_mid - geometric_mid) / geometric_mid = (spread²/8) / (1 - spread²/8) ≈ spread²/8` for small spreads, meaning the effective protection threshold is silently weakened by that fraction on every evaluation. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L185-245)
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

  /// @dev Per-share metrics in bin scaled units; shares floored at minimalMintableLiquidity.
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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L46-52)
```text
  ) external override returns (bytes4) {
    address pool_ = msg.sender;
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    // casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128 (same bound as pool)
    // forge-lint: disable-next-line(unsafe-typecast)
    uint128 midPrice = uint128(midPriceX64);

```
