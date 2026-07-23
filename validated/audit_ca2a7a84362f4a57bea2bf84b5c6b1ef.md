Audit Report

## Title
Silent Output Cap in Exact-Output Swap Delivers Less Than Requested Without Revert — (`metric-core/contracts/MetricOmmPool.sol`)

## Summary

`_swapToken1ForToken0SpecifiedOutput` silently clamps `amountOutScaled` to `binTotals.scaledToken0` when the requested output exceeds the pool's available token0, then continues execution and returns success. The caller receives fewer tokens than specified with no revert, no error code, and a `Swap` event reflecting only the reduced amount. The symmetric `_swapToken0ForToken1SpecifiedOutput` path contains the identical defect.

## Finding Description

When `swap()` is called with `zeroForOne = false` and `amountSpecified < 0` (exact-output), `_executeSwap` computes `amountOutScaled = TOKEN_0_SCALE_MULTIPLIER * uint256(-amountSpecified)` and passes it to `_swapToken1ForToken0SpecifiedOutput`. [1](#0-0) 

At the top of that function, inside an `unchecked` block, the cap fires silently:

```solidity
uint256 totalAvailableToken0Scaled = binTotals.scaledToken0;
if (amountOutScaled > totalAvailableToken0Scaled) {
    amountOutScaled = totalAvailableToken0Scaled;   // no revert
}
``` [2](#0-1) 

The function then proceeds normally through the swap loop, calls `_finalizeSwap`, and returns the capped actual output. The return at line 945 yields `amountOutScaled - state.amountSpecifiedRemainingScaled` — the capped delivered amount, not the originally requested amount. [3](#0-2) 

Back in `swap()`, the only post-callback check verifies that the pool received the input token — it does not verify that the output delivered equals the originally requested amount: [4](#0-3) 

The `priceLimitX64` guard at line 888 bounds the marginal execution price, not the total output volume, and does not compensate for this shortfall. [5](#0-4) 

The symmetric path `_swapToken0ForToken1SpecifiedOutput` contains the identical silent cap on `binTotals.scaledToken1`: [6](#0-5) 

## Impact Explanation

This is a swap conservation failure causing direct loss of user principal. An exact-output swap is a binding commitment: the caller specifies exactly how many tokens it must receive. Routers, aggregators, and on-chain integrators rely on this guarantee. When the pool silently delivers a smaller amount:

- A multi-hop router that forwards the output into a second pool receives less than expected and may revert after the first pool has already transferred input tokens, leaving the user with no recourse.
- A caller that relies on the exact-output specification as the guarantee (rather than independently verifying `amount0Delta`) suffers a direct shortfall in received tokens.
- The `Swap` event emits the capped `amount0Delta`, so off-chain monitoring also sees a "successful" swap at a reduced amount, masking the discrepancy. [7](#0-6) 

This meets the Sherlock High/Critical threshold: direct loss of user principal, broken core swap functionality, and swap conservation failure.

## Likelihood Explanation

No special permissions are required. Any unprivileged user or contract can trigger this by calling the public `swap()` entry point with `zeroForOne = false`, `amountSpecified < 0`, and a requested amount exceeding the pool's current token0 balance. This condition arises naturally as pool liquidity depletes through normal trading or after large LP removals. It is fully reachable with no privileged setup. [8](#0-7) 

## Recommendation

Replace the silent cap with an explicit revert in both `_swapToken1ForToken0SpecifiedOutput` and `_swapToken0ForToken1SpecifiedOutput`:

```solidity
uint256 totalAvailableToken0Scaled = binTotals.scaledToken0;
if (amountOutScaled > totalAvailableToken0Scaled) {
    revert InsufficientLiquidity();
}
```

This ensures that when the requested output exceeds available liquidity, the transaction reverts and the caller can handle the failure explicitly, rather than silently receiving a reduced amount.

## Proof of Concept

1. Deploy pool with `binTotals.scaledToken0 = 100e18` (100 token0 scaled).
2. Call `pool.swap(recipient, false, -200e6, priceLimitX64, callbackData, "")` — requesting 200 token0 exact-out.
3. `_executeSwap` computes `amountOutScaled = TOKEN_0_SCALE_MULTIPLIER * 200e6`.
4. `_swapToken1ForToken0SpecifiedOutput` is entered; the cap fires: `amountOutScaled` is silently reduced to `100e18`.
5. The swap loop executes against the capped amount, draining all token0 from bins.
6. `_finalizeSwap` writes the new bin state; `swap()` transfers only 100 token0 to `recipient`.
7. The callback is called with `amount0Delta = -100e6` (external units), not `-200e6`.
8. The transaction succeeds. The caller requested 200 token0 and received 100 — a 50% shortfall — with no revert and no on-chain error signal. [9](#0-8) [1](#0-0)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L265-277)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L943-948)
```text
      return (
        state.amountCalculatedScaled,
        amountOutScaled - state.amountSpecifiedRemainingScaled,
        state.protocolFeeAmountScaled,
        state.feeExclusiveInputScaled
      );
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1049-1052)
```text
        uint256 totalAvailableToken1Scaled = binTotals.scaledToken1;
        if (amountOutScaled > totalAvailableToken1Scaled) {
          amountOutScaled = totalAvailableToken1Scaled;
        }
```
