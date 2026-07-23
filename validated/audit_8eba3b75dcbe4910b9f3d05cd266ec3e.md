Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of end-user address, blocking legitimate router swaps or enabling full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender = msg.sender` of `pool.swap`, which is the router when users go through `MetricOmmSimpleRouter`. The extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][end_user]`, making per-user allowlisting impossible through the router and producing two mutually exclusive failure modes: DoS of allowlisted users or complete allowlist bypass.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap`, the pool's `msg.sender` is the router contract, not the end user: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`: [3](#0-2) 

The end user's address is never consulted. The `allowedSwapper` mapping is keyed by `(pool, swapper)` and is intended to gate individual swappers per pool, but the `sender` value arriving at the extension is structurally always the direct caller of `pool.swap` — the router — not the originating user.

## Impact Explanation
**Mode A (DoS — broken core swap functionality):** A pool admin allowlists `user` but not the router. The user calls `router.exactInputSingle(...)`. The extension checks `allowedSwapper[pool][router]` → `false` → reverts `NotAllowedToSwap`. The same user calling `pool.swap` directly succeeds. The standard periphery router is completely unusable for any pool using this extension with per-user entries, constituting broken core swap functionality.

**Mode B (Allowlist bypass — admin-boundary break via unprivileged path):** To enable router-mediated swaps at all, the pool admin must allowlist the router address. Once `allowedSwapper[pool][router] = true`, every user on the network — including those the admin never intended to allow — can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`. The allowlist provides no per-user protection.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is the primary public swap interface. Any pool that deploys `SwapAllowlistExtension` for per-user access control and expects users to use the router will immediately hit one of the two failure modes. No special conditions, timing, or attacker privileges are required — any unprivileged user calling the standard router on any allowlist-gated pool triggers the issue.

## Recommendation
Pass the original end-user address through the call chain. One approach: add a `swapper` parameter distinct from `sender` to the `beforeSwap` hook signature, where `swapper` is the address the pool admin intends to gate. The router can store `msg.sender` in transient storage and forward it via `extensionData`; the extension reads it from there. Alternatively, document that `SwapAllowlistExtension` gates the direct caller of `pool.swap` (not the end user) and require pool admins to allowlist the router address when router access is intended — but this eliminates per-user granularity entirely.

## Proof of Concept
```solidity
// Pool admin allowlists `user` but not the router
swapExtension.setAllowedToSwap(address(pool), user, true);

// Direct swap by user succeeds
vm.prank(user);
pool.swap(user, true, 1000, 0, "", ""); // passes: allowedSwapper[pool][user] = true

// Router swap by same user reverts
vm.prank(user);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    // ...
}));
// reverts: allowedSwapper[pool][router] = false
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```
