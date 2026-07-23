Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which resolves to `msg.sender` from the pool's call frame — the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual user's address. If the pool admin allowlists the router (required for allowlisted users to use the router), every user can bypass the per-user allowlist by routing through the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the first argument forwarded by `_beforeSwap`, which is `msg.sender` from the pool's own call frame — i.e., whoever called `pool.swap()`. [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` with `msg.sender = router`: [3](#0-2) 

The pool therefore passes `sender = router_address` to the extension. The allowlist check becomes `allowedSwapper[pool][router_address]` instead of `allowedSwapper[pool][actual_user]`. The same substitution occurs in `exactInput` and `exactOutputSingle` multi-hop paths. [4](#0-3) 

This creates an irreconcilable conflict for the pool admin:
- Allowlist the router → every user can swap (allowlist is bypassed)
- Do NOT allowlist the router → allowlisted users cannot use the router

There is no configuration that simultaneously restricts swaps to specific users **and** allows those users to enter through the router.

## Impact Explanation
Any non-allowlisted user can swap on a pool configured to restrict access to specific addresses (e.g., KYC-gated, institutional-only, or strategy-restricted pools) simply by calling the public `MetricOmmSimpleRouter` instead of calling `pool.swap()` directly. The allowlist guard is silently bypassed; no error is raised and the swap settles normally. This constitutes a broken core pool access-control flow with direct fund-flow consequences: unauthorized parties execute trades the pool admin explicitly intended to block. [5](#0-4) 

## Likelihood Explanation
The prerequisite is that the pool admin has allowlisted the router address. This is the natural and expected action for any pool admin who wants allowlisted users to be able to use the standard periphery entry point. The router is a public, well-known contract. Once the router is allowlisted, the bypass is trivially reachable by any EOA or contract — no special privilege, flash loan, or oracle manipulation is required. [6](#0-5) 

## Recommendation
The extension must check the economically relevant actor, not the immediate `pool.swap()` caller. Two viable approaches:

1. **Pass the real user through `extensionData`**: Have the router encode `msg.sender` (the actual user) into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router.

2. **Check `sender` and fall back to `extensionData`**: If `sender` is an allowlisted router, decode the real user from `extensionData` and apply the per-user check against that address.

Either approach requires a coordinated change to `MetricOmmSimpleRouter` (to inject the real user) and `SwapAllowlistExtension` (to consume it). [5](#0-4) 

## Proof of Concept
```
Setup:
  pool deployed with SwapAllowlistExtension
  pool admin: allowedSwapper[pool][alice]   = true   (alice is KYC'd)
  pool admin: allowedSwapper[pool][router]  = true   (to let alice use the router)

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls:
    pool.swap(bob, zeroForOne, amount, ...)   // msg.sender = router

  pool calls _beforeSwap(msg.sender=router, ...):
    passes sender=router to extension

  extension checks:
    allowedSwapper[pool][router] == true  ✓  → no revert

  Result: bob's swap settles; allowlist is bypassed.
``` [2](#0-1) [7](#0-6) [1](#0-0)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
