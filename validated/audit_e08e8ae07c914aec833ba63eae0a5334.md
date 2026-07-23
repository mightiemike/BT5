Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end user, allowing any caller to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual end user. Any pool admin who allowlists the router to support router-mediated swaps inadvertently opens the pool to all users, completely defeating the per-user access control mechanism.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` = pool, `sender` = whoever called `pool.swap()`): [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` with no mechanism to forward the original caller — the pool receives `msg.sender = router`: [4](#0-3) 

The complete call chain:
```
User → router.exactInputSingle()          [msg.sender = user]
     → pool.swap()                        [msg.sender = router]
     → _beforeSwap(sender = router, ...)
     → extension.beforeSwap(sender = router, ...)
     → allowedSwapper[pool][router]       ← checks router, not user
```

The end user's identity is completely lost. The extension can only observe the router address. This creates an irreconcilable dilemma: allowlisting individual users blocks router-mediated swaps for those users (router not allowlisted → revert), while allowlisting the router to support router-mediated swaps opens the pool to all users regardless of individual allowlist status. No configuration simultaneously enforces per-user access control and supports the standard router entry point.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` for per-user curation (KYC-only pools, institutional-only pools, pools restricted to specific counterparties) is completely bypassed by any user who routes through `MetricOmmSimpleRouter`. The allowlist policy — the sole access-control mechanism on the swap path — is rendered ineffective. Unauthorized users can execute swaps against restricted LP positions, directly impacting LP principal through trades the pool's access policy was designed to prevent. This constitutes a broken core pool functionality causing loss of funds and an admin-boundary break where the pool admin's access control configuration is bypassed by an unprivileged path.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin who deploys a `SwapAllowlistExtension` and also wants to support the standard router will naturally allowlist the router address, inadvertently opening the pool to all users. The bypass requires no special knowledge or privileged access — any user can call `exactInputSingle` on the router. The condition is trivially reachable by any unprivileged trader.

## Recommendation
The pool must forward the original end-user identity through the swap call so extensions can gate on the economically relevant actor. Two approaches:

1. **Add a `payer` parameter to `swap()`**: The pool accepts an explicit `payer` address (verified via callback), and passes it as `sender` to extensions instead of `msg.sender`. The router would pass `msg.sender` (the actual user) as `payer`. This is consistent with how `addLiquidity` already separates `msg.sender` (the payer/sender) from `owner` (the position holder).

2. **Extension-level forwarding via `extensionData`**: Require the router to encode the original caller in `extensionData`, and update `SwapAllowlistExtension` to decode and verify it (with a signature or trusted-forwarder pattern). This is more complex and requires the extension to trust the router.

Option 1 is cleaner and architecturally consistent with the existing liquidity flow design.

## Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

function test_swapAllowlist_bypassViaRouter() public {
    // Pool admin allowlists alice directly and the router for router-mediated swaps
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Alice can swap directly (sender = alice ✓)
    vm.prank(alice);
    pool.swap(alice, true, 1000, 0, "", "");

    // Bob (not allowlisted) bypasses the allowlist via the router
    // pool.swap() sees msg.sender = router (allowlisted) → passes
    // extension checks allowedSwapper[pool][router] = true → passes
    vm.prank(bob); // bob is NOT in allowedSwapper
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: token0,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: bob,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));
    // Bob's swap succeeds — allowlist completely bypassed
}
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
