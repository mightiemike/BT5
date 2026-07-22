### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any caller to bypass the per-user swap allowlist via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. A pool admin who adds the router to the allowlist (the only way to enable router-based swaps for their allowlisted users) inadvertently grants every unprivileged caller the ability to bypass the per-user allowlist by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist by checking the `sender` parameter: [1](#0-0) 

`sender` is populated by `MetricOmmPool.swap` as `msg.sender` of the pool call: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap`: [3](#0-2) 

So the extension sees `sender = router`, not the actual user. The allowlist check passes or fails based on whether the **router address** is in `allowedSwapper[pool]`, not whether the **user** is.

This creates an asymmetry identical in structure to the seeded bug: the guard is correctly applied on the direct-call path (`sender = user`) but is applied to the wrong actor on the router path (`sender = router`).

### Impact Explanation

A pool admin who wants to enable router-based swaps for their allowlisted users must add the router to `allowedSwapper[pool]`. Once the router is allowlisted, **every unprivileged caller** can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`. Non-allowlisted users execute swaps on a pool that was configured to restrict them, potentially draining LP funds or executing trades the pool admin explicitly intended to block. The allowlist invariant — "only allowlisted addresses may swap" — is broken on the router path.

### Likelihood Explanation

The `SwapAllowlistExtension` is a production periphery extension. Any pool that (a) deploys with this extension to restrict swappers and (b) also wants to support the canonical router must add the router to the allowlist. This is the only way to make the two features coexist, so the triggering condition is a natural, non-malicious pool admin action. Once the router is allowlisted, the bypass is available to any unprivileged caller with no further preconditions.

### Recommendation

The extension should check the **economically relevant actor** — the address that initiated the transaction — rather than the immediate caller of `pool.swap`. Two options:

1. **Check `recipient` instead of `sender`** — `recipient` is the address that receives the output tokens and is the economically relevant party. However, `recipient` can also be set to an arbitrary address by the router caller.

2. **Preferred: pass the original `msg.sender` through the router as part of `extensionData`** and have the extension decode and verify it. The router would encode `msg.sender` into `extensionData` before forwarding to the pool, and the extension would verify the decoded address against the allowlist. This preserves the original user identity across the router hop.

Alternatively, the extension documentation should explicitly warn that allowlisting the router grants access to all router users, and pool admins must manage allowlists at the router level or use direct-pool-only access patterns.

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, Alice, true)
   → allowedSwapper[pool][Alice] = true
3. Pool admin calls setAllowedToSwap(pool, router, true)
   → allowedSwapper[pool][router] = true
   (necessary to let Alice use the router)
4. Non-allowlisted Carol calls:
     MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient=Carol, ...)
   → pool calls _beforeSwap(sender=router, ...)
   → SwapAllowlistExtension checks allowedSwapper[pool][router] == true → PASSES
6. Carol's swap executes. The allowlist is bypassed.
```

Carol is never in `allowedSwapper[pool]`, yet her swap succeeds because the extension checked the router's allowlist entry, not hers. The broken invariant is: `allowedSwapper[pool][Carol] == false` yet Carol's swap settles. [1](#0-0) [4](#0-3) [5](#0-4)

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
