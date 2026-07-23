Audit Report

## Title
SwapAllowlistExtension validates the router address instead of the originating trader, allowing any non-allowlisted address to bypass the swap gate — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's `msg.sender` — the immediate caller of `pool.swap()`. When a trader routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the trader. If the router is allowlisted, any non-allowlisted trader can execute swaps against a restricted pool by routing through the router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- pool's msg.sender = router when called via router
  recipient,
  ...
  extensionData
);
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The originating EOA is never inspected. If `allowedSwapper[pool][router] = true`, the check passes for any trader who routes through the router, regardless of whether that trader is individually allowlisted.

Furthermore, there is no mechanism in the router to forward the originating trader's address to the extension: `exactInputSingle` passes `params.extensionData` through to the pool, but `SwapAllowlistExtension.beforeSwap` ignores `extensionData` entirely and only checks `sender`.

## Impact Explanation
A pool admin deploys `SwapAllowlistExtension` to restrict swaps to a curated set of addresses. If the admin also allowlists `MetricOmmSimpleRouter` — a natural and expected configuration for any pool that integrates with the periphery router — any arbitrary EOA can bypass the allowlist entirely by calling `router.exactInputSingle`. The extension's core invariant (gate swaps by individual swapper address) is broken: it is structurally impossible to simultaneously support router-based swaps and enforce per-trader allowlist restrictions, because the router collapses all originating traders into a single `sender` identity. This constitutes broken core pool functionality.

## Likelihood Explanation
The scenario requires only that the router is allowlisted on the pool — a common and expected configuration for any pool that integrates with the periphery router. No privileged access, malicious setup, or non-standard token behavior is needed. Any unprivileged EOA can exploit this by calling a public router function.

## Recommendation
The pool should pass the originating caller identity through the extension data or a dedicated field, or the router should forward `msg.sender` (the trader) as an explicit parameter. One concrete fix: `MetricOmmSimpleRouter` encodes `msg.sender` into `extensionData` before calling `pool.swap`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that value instead of (or in addition to) `sender`. Alternatively, the pool interface could be extended to carry an `originator` address distinct from `sender`.

## Proof of Concept
```solidity
function test_nonAllowlistedTraderBypassesViaRouter() public {
    // Setup: only router is allowlisted, trader is not
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // trader is NOT allowlisted

    // Trader calls router — pool sees sender=router, check passes
    vm.prank(trader);
    uint256 amountOut = router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token0),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1000,
            amountOutMinimum: 0,
            recipient: trader,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    assertGt(amountOut, 0);
    assertGt(token1.balanceOf(trader), 0);
}
```