### Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Allowing Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` as the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the allowlist checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. If the router is allowlisted (the natural setup for a pool that wants to support router-mediated swaps), any non-allowlisted user bypasses the gate entirely by calling through the router.

### Finding Description

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` as `msg.sender`: [4](#0-3) 

The pool therefore passes the **router address** as `sender` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

### Impact Explanation

Two fund-impacting outcomes follow directly:

**Bypass (high impact):** A pool admin who wants to support router-mediated swaps for allowlisted users must allowlist the router address. Once the router is allowlisted, `allowAllSwappers[pool]` is false but `allowedSwapper[pool][router]` is true, so the check passes for every caller regardless of their individual allowlist status. Any non-allowlisted user can trade on a curated pool by routing through `MetricOmmSimpleRouter`, defeating the entire access-control purpose of the extension.

**Broken functionality (medium impact):** If the pool admin does not allowlist the router, individually allowlisted users cannot use the router at all — their address is not the `sender` the extension sees. This breaks the standard swap path for legitimate users.

The bypass scenario directly enables disallowed parties to execute swaps on pools that were configured to restrict trading, which can drain LP value on pools designed for controlled or institutional liquidity.

### Likelihood Explanation

The router is a public, permissionless contract. Any user can call `router.exactInputSingle()` without any special privilege. The only precondition is that the pool admin has allowlisted the router (a natural and expected configuration). No admin cooperation or malicious setup is required beyond the normal operational pattern.

### Recommendation

The allowlist must gate the **economic actor**, not the immediate `pool.swap()` caller. Two approaches:

1. Have the router forward the original `msg.sender` through `extensionData`, and have `SwapAllowlistExtension` decode and check that value when `sender` is a known router.
2. Alternatively, check `sender` against the allowlist and also require that any intermediary (router) is itself allowlisted only as a pass-through, with the real user identity verified via a signed payload in `extensionData`.

The simplest safe fix is to not allowlist the router and instead require users to call `pool.swap()` directly for allowlisted pools, but this breaks the intended UX. The correct fix is identity forwarding through `extensionData`.

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps on a pool he was explicitly excluded from, receiving output tokens at the pool's oracle price. [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-41)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
