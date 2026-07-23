### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user. This means the allowlist either blocks all router-mediated swaps for legitimate users, or — if the router is allowlisted to fix that — any unprivileged user can bypass the curated pool's allowlist entirely by routing through the router.

### Finding Description

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router becomes `msg.sender` of `pool.swap()`: [4](#0-3) 

So `sender` delivered to the extension is the **router address**, not the end user. The allowlist check `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][endUser]`.

This creates an inescapable dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

- **Router not allowlisted**: Allowlisted users who attempt to swap through `MetricOmmSimpleRouter` are rejected — the extension reverts with `NotAllowedToSwap` even though the user is on the allowlist. Core router functionality is broken for the pool.
- **Router allowlisted** (the only fix for the above): The allowlist check degenerates to a single shared gate on the router address. Any unprivileged user can call `exactInputSingle` or `exactInput` through the router and the extension passes, defeating the entire purpose of the allowlist.

### Impact Explanation

**High.** A curated pool's allowlist — the primary access-control mechanism for permissioned pools — can be bypassed by any unprivileged user through the standard periphery router. The attacker does not need any special role, privileged key, or non-standard token. They simply route through `MetricOmmSimpleRouter` instead of calling `pool.swap()` directly. All swaps on the allowlisted pool that should have been blocked succeed, and LP funds in a curated pool are exposed to actors the pool admin explicitly excluded.

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the documented, production-grade entry point for swaps. Users are expected to use it. The bypass requires no setup beyond a normal router call. Any user who discovers the allowlist blocks their direct call can immediately retry through the router.

### Recommendation

The `sender` argument passed to extension hooks must represent the economically relevant actor — the end user — not the intermediary contract. Two approaches:

1. **Router-level forwarding**: Have the router encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check that value. This requires a trust assumption that the router is the only allowed intermediary.
2. **Direct-call enforcement**: Require that `pool.swap()` is only callable directly (no intermediary), or that the pool itself tracks the original initiator via a transient-storage pattern similar to how `inSwap` is tracked.

The cleanest invariant is: the allowlist must gate the same actor that the economic action is attributed to, regardless of which supported public entrypoint reaches the pool.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only `alice` is permitted.
3. Pool admin must also call `setAllowedToSwap(pool, router, true)` so that `alice` can use the router (otherwise `alice`'s router calls revert).
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — router is `msg.sender`.
6. Pool calls `_beforeSwap(msg.sender=router, ...)`.
7. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
8. `bob`'s swap executes successfully on a pool that was supposed to block him.

If the admin does **not** allowlist the router (step 3 omitted), then `alice`'s router call at step 4 reverts with `NotAllowedToSwap`, breaking the expected user flow for the legitimate allowlisted user. [3](#0-2) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
