### Title
Specified-output swaps with `priceLimitX64 == 0` silently return zero amounts due to missing zero-guard — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

The specified-input swap path correctly treats `priceLimitX64 == 0` as "no price limit" via an explicit `!= 0` guard. The specified-output swap path omits this guard, so any exact-output swap with `priceLimitX64 == 0` immediately returns `(0, 0, 0, 0)` — silently executing no trade — while the outer `swap()` call succeeds without reverting.

---

### Finding Description

In `_swapToken1ForToken0SpecifiedInput` (the exact-input path), the price-limit early-exit check reads:

```solidity
// line 979
if (params.priceLimitX64 != 0 && params.priceLimitX64 <= upperPriceX64) {
    break;
}
```

The `!= 0` guard ensures that when the caller passes `priceLimitX64 == 0` (meaning "no price limit"), the condition is skipped and the swap proceeds normally.

In `_swapToken1ForToken0SpecifiedOutput` (the exact-output path), the analogous early-exit check reads:

```solidity
// line 888
if (params.priceLimitX64 <= initialPriceX64) {
    return (0, 0, 0, 0);
}
```

The `!= 0` guard is absent. Because `initialPriceX64` is always strictly positive (the pool enforces `bid > 0` via `BidIsZero`), the condition `0 <= initialPriceX64` is unconditionally true whenever `priceLimitX64 == 0`. The function returns `(0, 0, 0, 0)` immediately — no swap is executed.

The outer `swap()` function receives `(amount0Delta = 0, amount1Delta = 0)`, transfers nothing to the recipient, calls the swap callback with `(0, 0)`, and the balance check `amount0Delta > 0 && ...` is false, so no revert occurs. The transaction succeeds silently with zero output. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

Any exact-output swap (`amountSpecified < 0`) submitted with `priceLimitX64 == 0` silently returns `(0, 0)`. The caller receives no output tokens, pays no input tokens, and the transaction does not revert. This is broken core pool swap functionality: a valid, documented input combination (`priceLimitX64 == 0` = no price limit) produces a silent no-op instead of executing the trade. Routers or integrators that rely on the return values to detect failure (rather than reverting) will silently deliver zero tokens to end users, constituting a direct loss of expected output for the trader. [3](#0-2) 

---

### Likelihood Explanation

`priceLimitX64 == 0` is a natural "no price limit" sentinel. The `swap()` function imposes no validation that `priceLimitX64 != 0`, and the specified-input path explicitly documents and handles this case. Any router (including `MetricOmmSimpleRouter`) that passes `priceLimitX64 == 0` for exact-output swaps — a common pattern for "fill at any price" — will trigger the bug on every such call. [4](#0-3) 

---

### Recommendation

Add the same `!= 0` guard to the early-exit check in `_swapToken1ForToken0SpecifiedOutput` (and the symmetric `_swapToken0ForToken1SpecifiedOutput` function):

```solidity
// Before (broken):
if (params.priceLimitX64 <= initialPriceX64) {
    return (0, 0, 0, 0);
}

// After (fixed):
if (params.priceLimitX64 != 0 && params.priceLimitX64 <= initialPriceX64) {
    return (0, 0, 0, 0);
}
```

This mirrors the guard already present in the specified-input path and restores consistent semantics: `priceLimitX64 == 0` means "no price limit" across all swap variants. [1](#0-0) 

---

### Proof of Concept

1. Pool is live with `bid = 1e18`, `ask = 1.01e18` (valid, non-zero).
2. Attacker/user calls `swap(recipient, false, -100e18, 0, callbackData, "")` — exact-output of 100 token0, no price limit.
3. `_executeSwap` dispatches to `_swapToken1ForToken0SpecifiedOutput` with `params.priceLimitX64 = 0`.
4. `initialPriceX64 > 0` (derived from positive bid/ask), so `0 <= initialPriceX64` is true.
5. Function returns `(0, 0, 0, 0)` immediately.
6. `amount0Delta = 0`, `amount1Delta = 0` — no tokens transferred.
7. Callback receives `(0, 0)`, balance check passes, `swap()` returns `(0, 0)` without reverting.
8. User receives 0 token0 despite expecting 100 token0; no funds are taken from them, but the intended trade is silently dropped. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-226)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

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

**File:** metric-core/contracts/MetricOmmPool.sol (L804-813)
```text
  function _getBidAndAskPriceX64() internal returns (uint128 bidPriceX64, uint128 askPriceX64) {
    address activePriceProvider = _resolvedPriceProvider();
    try IPriceProvider(activePriceProvider).getBidAndAskPrice() returns (uint128 bid, uint128 ask) {
      if (bid >= ask) revert BidGreaterThanAsk();
      if (bid == 0) revert BidIsZero();
      return (bid, ask);
    } catch (bytes memory reason) {
      revert PriceProviderFailed(reason);
    }
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L866-950)
```text
  function _swapToken1ForToken0SpecifiedOutput(uint256 amountOutScaled, SwapMath.InternalSwapParams memory params)
    internal
    returns (uint256, uint256, uint256, uint256)
  {
    unchecked {
      {
        uint256 totalAvailableToken0Scaled = binTotals.scaledToken0;
        if (amountOutScaled > totalAvailableToken0Scaled) {
          amountOutScaled = totalAvailableToken0Scaled;
        }
      }
      (
        BinState memory binState,
        SwapMath.SwapState memory state,
        int256 curBinIdxCache,
        uint256 curPosInBinCache,
        int256 curBinDistE6Cache,
        uint256 lowerPriceX64,
        uint256 upperPriceX64,
        uint256 initialPriceX64
      ) = _getInitialStateForSwap(false, true, params, amountOutScaled);

      if (params.priceLimitX64 <= initialPriceX64) {
        return (0, 0, 0, 0);
      }

      while (state.amountSpecifiedRemainingScaled > 0) {
        bool nonEmptyBin = true;
        if (binState.token0BalanceScaled == 0 || curPosInBinCache >= type(uint104).max) {
          if (params.priceLimitX64 <= upperPriceX64) {
            break;
          }
          nonEmptyBin = false;
        }

        if (nonEmptyBin) {
          int256 delta0Scaled;
          int256 delta1Scaled;
          uint256 binLpFeeAmountScaled;

          (curPosInBinCache, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) = SwapMath.buyToken0InBinSpecifiedOut(
            binState,
            curPosInBinCache,
            state,
            params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
            lowerPriceX64,
            upperPriceX64,
            params.priceLimitX64,
            spreadFeeE6
          );

          emit BinSwapped(
            curBinIdxCache,
            BinBalanceDelta({delta0Scaled: delta0Scaled, delta1Scaled: delta1Scaled}),
            binLpFeeAmountScaled
          );
          _saveBinState(curBinIdxCache, binState);
        }

        if (curPosInBinCache >= type(uint104).max || !nonEmptyBin) {
          if (curBinIdxCache >= HIGHEST_BIN) {
            break;
          }
          curBinIdxCache++;
          curPosInBinCache = 0;
          curBinDistE6Cache = _clampInt256ToInt24(_addDistE6(int256(curBinDistE6Cache), binState.lengthE6));

          lowerPriceX64 = upperPriceX64;
          binState = _binStates[curBinIdxCache];
          upperPriceX64 = distanceE6ToPriceX64(_addDistE6(curBinDistE6Cache, binState.lengthE6), params.midPriceX64);
        } else {
          break;
        }
      }

      _finalizeSwap(curBinIdxCache, curPosInBinCache, curBinDistE6Cache);

      return (
        state.amountCalculatedScaled,
        amountOutScaled - state.amountSpecifiedRemainingScaled,
        state.protocolFeeAmountScaled,
        state.feeExclusiveInputScaled
      );
    }
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L977-981)
```text
        bool nonEmptyBin = true;
        if (binState.token0BalanceScaled == 0 || curPosInBinCache >= type(uint104).max) {
          if (params.priceLimitX64 != 0 && params.priceLimitX64 <= upperPriceX64) {
            break;
          }
```
