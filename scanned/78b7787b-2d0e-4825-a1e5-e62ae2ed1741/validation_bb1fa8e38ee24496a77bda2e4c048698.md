### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the actual user. If the pool admin allowlists the router (a natural step to enable periphery usage for their curated users), every user — including those not individually allowlisted — can bypass the swap guard by routing through the router.

---

### Finding Description

**Actor binding in the pool's `swap` function**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**What the extension actually checks**

`SwapAllowlistExtension.beforeSwap` gates on `sender` (the first argument), keyed by `msg.sender` (the pool): [3](#0-2) 

**What the router passes as `msg.sender` to the pool**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

The same holds for `exactInput` (every hop), `exactOutputSingle`, and `exactOutput` — the router is always the direct caller of `pool.swap()`. [5](#0-4) 

**Result**: the allowlist check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The extension is gating the wrong actor.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants their allowlisted users to be able to use the standard periphery will allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`, and the `beforeSwap` guard passes for **every** call that arrives through the router — regardless of who the actual end-user is. Any address can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router and swap on the curated pool, completely bypassing the intended per-user restriction. The allowlist guard fails open for the entire public periphery path.

---

### Likelihood Explanation

The trigger condition is that the pool admin allowlists the router. This is the natural, expected action for any pool admin who wants their allowlisted users to be able to use the standard periphery rather than calling the pool directly. The admin has no on-chain signal that doing so opens the pool to all users; the `isAllowedToSwap` view function will return `true` for the router and appear correct. The bypass is therefore reachable through normal, documented usage of the protocol.

---

### Recommendation

The `SwapAllowlistExtension` must gate the actual end-user, not the intermediary. Two concrete options:

1. **Extension-data forwarding**: Have the router encode the real user address into `extensionData` for the swap allowlist extension, and have the extension decode and check that address when `sender` is a known router. This requires a convention between the router and the extension.

2. **Check `sender` only when it is not a trusted router**: Add a router registry to the extension so that when `sender` is a registered router, the extension falls back to checking an address decoded from `extensionData` (supplied by the router as `msg.sender` at the router level).

The simplest safe default is to document that the router must **never** be added to the swap allowlist, and that allowlisted users must call `pool.swap()` directly. However, this breaks the intended periphery UX and is not a code-level fix.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable periphery for Alice
  - Pool admin calls setAllowedToSwap(pool, Alice, true)    // Alice is the intended user

Attack:
  - Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
        pool=pool, recipient=Bob, ...
    )
  - Router calls pool.swap(Bob, ...) — msg.sender to pool = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Guard passes; Bob's swap executes on the curated pool
  - Bob has bypassed the allowlist entirely
``` [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
