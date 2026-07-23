Audit Report

## Title
SwapAllowlistExtension validates the router address instead of the originating trader, allowing any non-allowlisted address to bypass the swap gate — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's `msg.sender` — the immediate caller of `pool.swap()`. When a trader routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the trader. If the router is allowlisted, any non-allowlisted trader can execute swaps against a restricted pool, fully bypassing the access-control gate.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap(), i.e. the router
  recipient,
  ...
);
```

When a trader calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
  );
```

So the pool sees `msg.sender = router`. The extension then evaluates:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. If `allowedSwapper[pool][router] = true`, the check passes regardless of who the originating trader is. The trader's address is never inspected. No existing guard in the extension or pool recovers the originating EOA.

## Impact Explanation
A pool admin deploys `SwapAllowlistExtension` to restrict swaps to a curated set of addresses. If the admin also allowlists `MetricOmmSimpleRouter` — a natural and expected configuration for any pool that integrates with the periphery router — any arbitrary EOA can bypass the allowlist entirely by calling `router.exactInputSingle`. This breaks the core access-control invariant of the extension and constitutes broken core pool functionality: the pool's swap gate is rendered ineffective for all traders who route through the router.

## Likelihood Explanation
The scenario requires only that the router is allowlisted on the pool, which is a common and expected configuration for any pool that integrates with the periphery router. No privileged access, malicious setup, or non-standard token behavior is needed. Any unprivileged EOA can exploit this by calling a public router function.

## Recommendation
The router should encode `msg.sender` (the originating trader) into `extensionData` before calling `pool.swap`, and `SwapAllowlistExtension.beforeSwap` should decode and check that value instead of (or in addition to) `sender`. Alternatively, the pool interface could be extended to carry an `originator` address distinct from `sender`, passed through the full call chain to the extension.

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
    // Assert: swap succeeded and trader received output despite not being allowlisted
    assertGt(amountOut, 0);
    assertGt(token1.balanceOf(trader), 0);
}
```