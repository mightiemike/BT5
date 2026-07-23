Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap()` receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. A pool admin who allowlists the router to permit router-mediated swaps inadvertently grants every user in the world unrestricted access to the curated pool.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `sender` (the router) against the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly with no forwarding of the original `msg.sender`: [4](#0-3) 

The pool sees `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. Two invariants break:

1. **Allowlist bypass:** A pool admin who allowlists the router (the only way to permit router-mediated swaps on a curated pool) causes `allowedSwapper[pool][router] == true` for every call, so every user — including those explicitly excluded — can swap freely by routing through `MetricOmmSimpleRouter`.
2. **Broken core functionality:** A pool admin who allowlists specific users but not the router causes those users to be unable to use the router at all. Their address is allowlisted, but the extension sees the router and reverts.

The same misbinding affects `exactInput`, `exactOutputSingle`, and `exactOutput` in the router. [5](#0-4) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker requires no special privilege — `MetricOmmSimpleRouter` is a public, permissionless contract. The pool admin's allowlist configuration is silently rendered ineffective, allowing any address to execute swaps on a pool intended to be private, draining LP liquidity at oracle-quoted prices. This constitutes a broken core pool functionality causing loss of funds and an admin-boundary break where an unprivileged path bypasses the pool admin's access control.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the router is immediately vulnerable. The bypass requires no special setup: any address calls `exactInputSingle` on the router pointing at the curated pool. The condition is trivially reachable by any unprivileged trader.

## Recommendation

The extension must check the economically relevant actor — the end user — not the intermediary. The cleanest fix is for `MetricOmmSimpleRouter` to embed the original `msg.sender` in `extensionData` (e.g., as an ABI-encoded prefix), and for `SwapAllowlistExtension.beforeSwap` to decode and check that value when `extensionData` is non-empty. Alternatively, the pool can pass the original caller's identity through a dedicated field rather than relying on `msg.sender`, which is always the immediate caller (the router). A third option is to document and enforce at the factory level that `SwapAllowlistExtension` is incompatible with router-mediated swaps.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps; all other addresses remain un-allowlisted.
3. Attacker (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle({pool: curated_pool, ...})`.
4. Router calls `pool.swap(...)` with `msg.sender = router`.
5. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` evaluates `allowedSwapper[pool][router] == true` → passes.
6. Attacker's swap executes against the curated pool's LP liquidity despite never being allowlisted.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-165)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
