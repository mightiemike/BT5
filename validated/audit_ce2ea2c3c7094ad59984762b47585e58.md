Audit Report

## Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass the Per-User Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument — the direct caller of `MetricOmmPool.swap` — against the per-pool allowlist. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address rather than the end user's address. A pool admin who allowlists the router to enable router-mediated swaps for their intended users inadvertently opens the pool to every caller of the public router, completely nullifying the per-user access control.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value to every configured extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct pool caller: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making `msg.sender` from the pool's perspective the router address, not the end user: [3](#0-2) 

The pool therefore passes the **router's address** as `sender` to the extension. The extension evaluates `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (the natural and expected action to serve their allowlisted users via the router), the check passes for **every caller of the router**, regardless of whether that caller is individually allowlisted. There is no secondary check on the originating user address anywhere in the call chain.

## Impact Explanation
The `SwapAllowlistExtension` is the sole access-control mechanism for the swap path. Any user — including those the pool admin explicitly excluded — can execute swaps on an allowlisted pool by routing through `MetricOmmSimpleRouter`. This constitutes a broken core pool functionality and an admin-boundary break: the pool admin's intent to restrict swap access is silently violated. For pools used as KYC-gated, institutional, or stop-loss-guarded venues, this enables unauthorized fund flows and unauthorized price impact by unprivileged actors.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. A pool admin who configures `SwapAllowlistExtension` and also wants their allowlisted users to be able to use the router has no mechanism other than allowlisting the router address itself — `setAllowedToSwap(pool, router, true)`. This is the natural and expected administrative action; the admin has no way to know it opens the pool to all router users. Once the admin takes this reasonable step, any public user can exploit it without any privileged access, repeatedly and without detection.

## Recommendation
Pass the **original end-user address** through the extension call chain rather than the direct pool caller. One approach: add an `originator` field to the swap parameters (analogous to how `recipient` is already separated from `sender`) that the pool populates from a trusted periphery context — for example, transient storage set by the router before calling the pool (the router already uses transient storage for callback context via `_setNextCallbackContext`). The extension would then gate on `originator` instead of `sender`. Alternatively, document clearly that the allowlist gates the direct pool caller only, and that router-mediated swaps must never be enabled on allowlisted pools — but this is a documentation-only mitigation that does not fix the code-level flaw.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps for their intended users. [4](#0-3) 
3. `bob` (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. The router calls `pool.swap(params.recipient, ...)` — `msg.sender` from the pool's perspective = router address. [3](#0-2) 
5. Pool calls `extension.beforeSwap(router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` = `true` → does not revert. [2](#0-1) 
7. `bob`'s swap executes successfully despite never being individually allowlisted.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
