### Title
SwapAllowlistExtension Gates Router Address Instead of Actual User, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router**, not the end user. If the pool admin allowlists the router to support router-mediated swaps, every user — including explicitly disallowed ones — can bypass the per-user allowlist by routing through the router.

### Finding Description

**Call chain for a direct swap (correct):**

```
user → pool.swap()
  pool: _beforeSwap(msg.sender=user, ...)
  ExtensionCalling: encodes sender=user
  SwapAllowlistExtension: checks allowedSwapper[pool][user]  ✓
```

**Call chain for a router-mediated swap (broken):**

```
user → router.exactInputSingle()
  router → pool.swap()   (msg.sender to pool = router)
  pool: _beforeSwap(msg.sender=router, ...)
  ExtensionCalling: encodes sender=router
  SwapAllowlistExtension: checks allowedSwapper[pool][router]  ✗
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called `pool.swap()` — the router when the user goes through the periphery: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original `msg.sender` as the swapper identity: [4](#0-3) 

The pool's `swap` signature has no explicit `sender` parameter; the pool always uses `msg.sender`: [5](#0-4) 

This creates an irreconcilable mismatch: the allowlist can gate either the router (allowing all users through it) or no router at all (blocking allowlisted users from using the periphery). There is no configuration that correctly gates individual users who arrive via the router.

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (the natural configuration for a pool that supports the standard periphery) inadvertently opens the allowlist to every user. Any disallowed address can call `router.exactInputSingle` or `router.exactInput` and trade on the curated pool without restriction. The allowlist provides zero protection against router-mediated swaps, which is the primary supported swap path in the protocol.

This is a direct bypass of a configured access-control guard, allowing unauthorized users to execute swaps on pools that were explicitly designed to restrict trading to a curated set of addresses.

### Likelihood Explanation

- The router is the standard, documented swap entrypoint for end users.
- Any pool that uses `SwapAllowlistExtension` and wants to support normal periphery usage must allowlist the router, triggering the bypass automatically.
- No special permissions, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices.
- The attacker is any address not on the allowlist.

### Recommendation

The pool's `swap` interface must be extended with an explicit `originator` or `onBehalfOf` field that the router populates with `msg.sender` before calling the pool, and the pool must forward that field to extensions. Alternatively, `SwapAllowlistExtension` should read the actual user identity from authenticated router-supplied `extensionData` (signed or verified by the router itself). Without one of these changes, `SwapAllowlistExtension` cannot correctly gate individual users on router-mediated swap paths.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — the natural step to enable router support.
3. Pool admin calls `setAllowedToSwap(pool, alice, false)` — alice is explicitly disallowed.
4. Alice calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Alice successfully trades on a pool she was explicitly barred from, with no revert. [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
