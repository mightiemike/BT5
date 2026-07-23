Audit Report

## Title
Silent Output Cap in Exact-Output Swap Delivers Less Than Requested Without Revert — (`metric-core/contracts/MetricOmmPool.sol`)

## Summary

`_swapToken1ForToken0SpecifiedOutput` and its symmetric counterpart `_swapToken0ForToken1SpecifiedOutput` silently clamp the requested output amount to the pool's total available token balance when the request exceeds pool liquidity, instead of reverting. The transaction succeeds, the caller receives fewer tokens than specified, and no error signal is emitted. This breaks the exact-output swap guarantee that callers, routers, and aggregators depend on.

## Finding Description

In `_swapToken1ForToken0SpecifiedOutput`, the first action inside the `unchecked` block is:

```solidity
uint256 totalAvailableToken0Scaled = binTotals.scaledToken0;
if (amountOutScaled > totalAvailableToken0Scaled) {
    amountOutScaled = totalAvailableToken0Scaled;   // silent cap — no revert
}
``` [1](#0-0) 

The capped `amountOutScaled` is then passed to `_getInitialStateForSwap`, the swap loop executes against the reduced target, and `_finalizeSwap` commits the state. The function returns the capped `amountOutScaled - state.amountSpecifiedRemainingScaled` as the actual output. [2](#0-1) 

The symmetric path `_swapToken0ForToken1SpecifiedOutput` (called when `zeroForOne = true`, `amountSpecified < 0`) contains the identical silent-cap pattern for token1:

```solidity
uint256 totalAvailableToken1Scaled = binTotals.scaledToken1;
if (amountOutScaled > totalAvailableToken1Scaled) {
    amountOutScaled = totalAvailableToken1Scaled;
}
``` [3](#0-2) 

Back in `swap()`, the `!zeroForOne` branch transfers the capped `amount0Delta` to the recipient with no comparison against the originally requested `amountSpecified`: [4](#0-3) 

The only post-callback check verifies that the pool received enough input token (i.e., `balance1Before + amount1Delta <= balance1()`), not that the output matched the caller's specification: [5](#0-4) 

The `priceLimitX64` guard does not compensate: it bounds the marginal execution price, not the total output volume. A nearly-empty pool can satisfy the price limit while delivering a fraction of the requested output. [6](#0-5) 

The `Swap` event emits the capped `amount0Delta`, so off-chain monitoring also observes a "successful" swap at the reduced amount, masking the discrepancy. [7](#0-6) 

## Impact Explanation

This is a **swap conservation failure / direct loss of user principal**. An exact-output swap is a binding commitment: the caller specifies exactly how many tokens it must receive. Routers, aggregators, and on-chain integrators built on `MetricOmmPool` rely on this guarantee to enforce their own minimum-output checks. When the pool silently delivers a smaller amount, the caller's downstream logic (e.g., a multi-hop router forwarding the output into a second pool) receives less than expected and may revert after the user's input token has already been transferred with no recourse. A caller that relies on the exact-output specification as the guarantee suffers a direct shortfall in received tokens. This meets the Sherlock Critical/High threshold for direct loss of user principal and broken core swap functionality. [8](#0-7) 

## Likelihood Explanation

No special permissions are required. Any user or contract can trigger this by submitting an exact-output swap (`amountSpecified < 0`) for an amount larger than the pool's current token balance. This condition arises naturally as pool liquidity depletes through normal trading or after large LP removals. It is reachable through the public `swap()` entry point with no privileged setup. [9](#0-8) 

## Recommendation

Replace the silent cap with an explicit revert in both `_swapToken1ForToken0SpecifiedOutput` and `_swapToken0ForToken1SpecifiedOutput`:

```solidity
uint256 totalAvailableToken0Scaled = binTotals.scaledToken0;
if (amountOutScaled > totalAvailableToken0Scaled) {
    revert InsufficientLiquidity();   // revert instead of cap
}
```

This ensures the transaction fails explicitly when the requested output exceeds available liquidity, allowing callers to handle the failure rather than silently receiving a reduced amount. [1](#0-0) [3](#0-2) 

## Proof of Concept

1. Deploy pool with `binTotals.scaledToken0 = 100e18` (100 token0 scaled).
2. Call `pool.swap(recipient, false, -200e6, priceLimitX64, callbackData, "")` — requesting 200 token0 exact-out.
3. `_executeSwap` computes `amountOutScaled = TOKEN_0_SCALE_MULTIPLIER * 200e6`, enters `_swapToken1ForToken0SpecifiedOutput`.
4. The cap fires at L873-874: `amountOutScaled` is silently reduced to `100e18`.
5. The swap loop executes against the capped amount, draining all token0 from bins.
6. `_finalizeSwap` commits state; `swap()` transfers only 100 token0 to `recipient` at L268.
7. The callback is invoked with `amount0Delta = -100e6` (external units), not `-200e6`.
8. The post-callback check at L275 only verifies token1 input was received — it passes.
9. Transaction succeeds. Caller requested 200 token0, received 100 — a 50% shortfall — with no revert and no on-chain error signal. [10](#0-9)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-225)
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

**File:** metric-core/contracts/MetricOmmPool.sol (L264-277)
```text
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
```

**File:** metric-core/contracts/MetricOmmPool.sol (L297-300)
```text
    emit Swap(
      msg.sender, recipient, amountSpecified > 0, amount0Delta, amount1Delta, curBinIdx, curPosInBin, protocolFeeAmount
    );
    return (amount0Delta.toInt128(), amount1Delta.toInt128());
```

**File:** metric-core/contracts/MetricOmmPool.sol (L716-726)
```text
        } else {
          // forge-lint: disable-next-line(unsafe-typecast)
          uint256 amountOutScaled = TOKEN_0_SCALE_MULTIPLIER * uint256(-amountSpecified);
          uint256 amountInScaled;
          (amountInScaled, amountOutScaled, protocolFeeScaled, feeExclusiveInputScaled) =
            _swapToken1ForToken0SpecifiedOutput(amountOutScaled, params);
          // forge-lint: disable-next-line(unsafe-typecast)
          amount0DeltaScaled = -int256(amountOutScaled);
          // forge-lint: disable-next-line(unsafe-typecast)
          amount1DeltaScaled = int256(amountInScaled);
        }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L871-876)
```text
      {
        uint256 totalAvailableToken0Scaled = binTotals.scaledToken0;
        if (amountOutScaled > totalAvailableToken0Scaled) {
          amountOutScaled = totalAvailableToken0Scaled;
        }
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L888-890)
```text
      if (params.priceLimitX64 <= initialPriceX64) {
        return (0, 0, 0, 0);
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L941-948)
```text
      _finalizeSwap(curBinIdxCache, curPosInBinCache, curBinDistE6Cache);

      return (
        state.amountCalculatedScaled,
        amountOutScaled - state.amountSpecifiedRemainingScaled,
        state.protocolFeeAmountScaled,
        state.feeExclusiveInputScaled
      );
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1048-1053)
```text
      {
        uint256 totalAvailableToken1Scaled = binTotals.scaledToken1;
        if (amountOutScaled > totalAvailableToken1Scaled) {
          amountOutScaled = totalAvailableToken1Scaled;
        }
      }
```
