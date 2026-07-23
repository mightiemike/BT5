Audit Report

## Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass the Per-User Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument — which is `msg.sender` of `MetricOmmPool.swap` — against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's address. A pool admin who allowlists the router to enable router-mediated swaps for their intended users inadvertently opens the pool to every caller of the public router, completely defeating the per-user access control.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as `sender` in the `abi.encodeCall` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct pool caller: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly — `msg.sender` of that call is the router contract: [4](#0-3) 

The router stores the real end-user address only in transient storage as the `payer` for the callback, not in any field visible to the pool or its extensions: [5](#0-4) 

The pool therefore passes the **router's address** as `sender` to the extension. The extension evaluates `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router address (the only way to permit router-mediated swaps for their intended users), the check passes for **every caller of the router**, regardless of whether that caller is individually allowlisted. There is no mechanism in the current architecture for the extension to recover the original end-user address.

## Impact Explanation
Any user — including those the pool admin explicitly excluded — can execute swaps on an allowlisted pool by routing through `MetricOmmSimpleRouter`. The allowlist, which is the sole access-control mechanism for the swap path, is rendered ineffective for all router-mediated swaps. Depending on the pool's purpose (e.g., restricted institutional pool, KYC-gated pool, or a pool with a stop-loss extension that relies on the allowlist to limit who can trigger it), this results in unauthorized fund flows and unauthorized price impact. The broken invariant is the value stored in `allowedSwapper[pool][sender]`: the extension reads the router address where it should read the end-user address, causing the guard's decision to be based on the wrong identity. This constitutes a broken core pool access-control mechanism with direct fund-flow impact.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. A pool admin who configures `SwapAllowlistExtension` and also wants their allowlisted users to be able to use the router has no mechanism to do so other than allowlisting the router address itself — this is the natural and expected administrative action. The admin has no way to know it opens the pool to all router users. The trigger requires only that the admin has taken this reasonable step, after which any public user can exploit it without any privileged access, repeatedly, across all router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

## Recommendation
Pass the original end-user address through the extension call chain rather than the direct pool caller. One approach: add an `originator` field to the swap parameters (similar to how `recipient` is already separated from `sender`) that the pool populates from a trusted periphery context — for example, transient storage set by the router before calling the pool, readable by the extension. The extension would then gate on `originator` instead of `sender`. Alternatively, document clearly that the allowlist gates the direct pool caller only and that router-mediated swaps must never be enabled on allowlisted pools, and enforce this at the factory level by preventing simultaneous configuration of both.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps (intending to serve their allowlisted users via the router).
3. `bob` (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. The router calls `pool.swap(params.recipient, ...)` — `msg.sender` at the pool = router address.
5. Pool calls `_beforeSwap(router, ...)` → `ExtensionCalling` encodes `sender = router` → calls `extension.beforeSwap(router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` = `true` → does not revert.
7. `bob`'s swap executes successfully despite never being individually allowlisted.

Foundry test plan: deploy pool with `SwapAllowlistExtension`, allowlist only the router address, call `exactInputSingle` from an address that is not individually allowlisted, assert the swap succeeds (demonstrating bypass), then call `pool.swap` directly from the same non-allowlisted address and assert it reverts with `NotAllowedToSwap`.

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

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L29-32)
```text
  function _setNextCallbackContext(address pool, uint8 callbackMode, address payer, address tokenToPay) internal {
    _requireFactoryPool(pool);
    TransientCallbackPool.set(pool, callbackMode, payer, tokenToPay);
  }
```
