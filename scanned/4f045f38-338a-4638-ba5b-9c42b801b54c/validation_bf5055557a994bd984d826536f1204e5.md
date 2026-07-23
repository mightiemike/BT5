### Title
Exact-Output Swap Silently Caps Output to Available Liquidity Instead of Reverting — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

Both exact-output swap paths in `MetricOmmPool` silently reduce the requested output amount to the pool's available balance when liquidity is insufficient, rather than reverting. A direct pool caller requesting a specific output amount receives less (potentially zero) without any error, while the transaction succeeds and pool state is mutated.

---

### Finding Description

In `_swapToken0ForToken1SpecifiedOutput` and `_swapToken1ForToken0SpecifiedOutput`, before the swap loop begins, the requested output is unconditionally clamped to the total available scaled balance:

```solidity
// _swapToken0ForToken1SpecifiedOutput (lines 1049–1052)
uint256 totalAvailableToken1Scaled = binTotals.scaledToken1;
if (amountOutScaled > totalAvailableToken1Scaled) {
    amountOutScaled = totalAvailableToken1Scaled;  // silent cap, no revert
}
``` [1](#0-0) 

The same pattern appears in the token0-output path:

```solidity
// _swapToken1ForToken0SpecifiedOutput (lines 872–875)
uint256 totalAvailableToken0Scaled = binTotals.scaledToken0;
if (amountOutScaled > totalAvailableToken0Scaled) {
    amountOutScaled = totalAvailableToken0Scaled;  // silent cap, no revert
}
``` [2](#0-1) 

After capping, the swap loop runs against the reduced target. If the pool is fully drained (e.g., by a sandwich attacker), `amountOutScaled` becomes `0`, the loop body is skipped, and `_executeSwap` returns `(amountIn=0, amountOut=0, fee=0)`. Back in `swap()`, both `amount0Delta` and `amount1Delta` are `0`, so no token transfer occurs, the callback is invoked with `(0, 0)`, the balance check `amount0Delta > 0 && ...` is false, and the transaction completes successfully — delivering zero output to the caller. [3](#0-2) 

The `MetricOmmSimpleRouter` does add a post-swap equality check for its own `exactOutputSingle` and `exactOutput` paths:

```solidity
if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);
``` [4](#0-3) 

However, this guard lives entirely in the periphery router, not in the pool. Any caller that interacts with `MetricOmmPool.swap()` directly — including other smart contracts, integrators, or aggregators — receives no such protection. The pool itself never reverts on an unfulfillable exact-output request.

---

### Impact Explanation

**Direct loss of expected output for non-router callers.** A contract that calls `pool.swap(recipient, zeroForOne, -amountOut, ...)` expecting to receive exactly `amountOut` tokens (e.g., to repay a flash loan, settle a debt, or satisfy a downstream invariant) will instead receive an arbitrary lesser amount — including zero — with no on-chain signal of failure. The transaction succeeds, pool state (`curBinIdx`, `curPosInBin`, `curBinDistFromProvidedPriceE6`) is mutated, and the caller's downstream logic operates on a wrong balance.

**MEV sandwich attack (exact analog to H-04):**
1. Attacker observes a pending direct-pool exact-output swap for `X` token1.
2. Attacker front-runs: removes all token1 liquidity from the pool.
3. Victim's swap executes: `amountOutScaled` is capped to `0`, swap loop is skipped, victim receives `0` token1, pays `0` token0 — transaction succeeds silently.
4. Attacker back-runs: re-adds liquidity at the now-shifted price, profiting from the price impact of the drain.

The victim's transaction does not revert; they simply receive nothing while the pool's internal cursor state has been updated.

---

### Likelihood Explanation

Any integrator or aggregator that calls `MetricOmmPool.swap()` directly with a negative `amountSpecified` (exact-output mode) and does not independently verify the returned deltas is vulnerable. This is a realistic integration pattern. The pool's public interface gives no indication that exact-output requests may be silently downgraded; the function signature and NatSpec imply the caller receives the requested amount. Likelihood is **medium** — it requires direct pool interaction rather than router use, but the pool is a public contract and direct integration is a standard pattern.

---

### Recommendation

The pool should revert when it cannot fulfill an exact-output request. After the swap loop completes, check whether `state.amountSpecifiedRemainingScaled > 0` and revert if so:

```solidity
// After the while loop in _swapToken0ForToken1SpecifiedOutput and _swapToken1ForToken0SpecifiedOutput:
if (state.amountSpecifiedRemainingScaled > 0) {
    revert InsufficientLiquidity();
}
```

Alternatively, revert immediately at the cap site if the requested amount exceeds available liquidity:

```solidity
uint256 totalAvailableToken1Scaled = binTotals.scaledToken1;
if (amountOutScaled > totalAvailableToken1Scaled) {
    revert InsufficientLiquidity();
}
```

This mirrors the fix recommended in H-04 for the yAxis controller and ensures the pool fails closed rather than silently delivering a degraded result.

---

### Proof of Concept

```solidity
// Attacker drains all token1 from the pool (via removeLiquidity or prior swaps).
// Victim calls pool.swap() directly for exact output:
(int128 d0, int128 d1) = pool.swap(
    victim,
    true,           // zeroForOne
    -1_000e18,      // exact output: 1000 token1
    0,              // open price limit
    "",
    ""
);
// d0 == 0, d1 == 0 — victim receives 0 token1, pays 0 token0.
// Transaction succeeds. No revert. Pool state mutated.
assert(d1 == 0); // victim got nothing
```

The `_swapToken0ForToken1SpecifiedOutput` caps `amountOutScaled` to `binTotals.scaledToken1 = 0`, the loop body never executes, and `(0, 0, 0, 0)` is returned up the call stack. [5](#0-4) [6](#0-5)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L866-876)
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
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1043-1053)
```text
  function _swapToken0ForToken1SpecifiedOutput(uint256 amountOutScaled, SwapMath.InternalSwapParams memory params)
    internal
    returns (uint256, uint256, uint256, uint256)
  {
    unchecked {
      {
        uint256 totalAvailableToken1Scaled = binTotals.scaledToken1;
        if (amountOutScaled > totalAvailableToken1Scaled) {
          amountOutScaled = totalAvailableToken1Scaled;
        }
      }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L138-139)
```text
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);
```
