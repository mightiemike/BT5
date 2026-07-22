### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual end-user. If the router is allowlisted (required for any allowlisted user to use it), every non-allowlisted user can bypass the curated pool's swap allowlist by routing through the public router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value and dispatches it to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension is the pool): [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

From the pool's perspective `msg.sender` is the router, so `sender` delivered to the extension is the **router address**, not the actual end-user. The allowlist check therefore becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for pool admins:

- **Router not allowlisted**: allowlisted users cannot use the router at all — the extension reverts on every router-mediated swap.
- **Router allowlisted**: the check collapses to a single bit (`allowedSwapper[pool][router] == true`), and every user — allowlisted or not — can swap by routing through the public router.

The same structural problem applies to `exactInput`, `exactOutputSingle`, and `exactOutput` paths in the router, all of which call `pool.swap()` with `msg.sender = router`. [5](#0-4) 

### Impact Explanation

A curated pool (KYC-only, institutional-only, or otherwise restricted) that deploys `SwapAllowlistExtension` and allowlists the router loses all per-user access control for every swap that enters through `MetricOmmSimpleRouter`. Any unprivileged user can trade in the pool, defeating the curation policy entirely. Because the pool is oracle-driven and the attacker controls swap direction and size, this can be used to extract value from LP positions at prices the pool designers intended to restrict to vetted counterparties.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint documented in the periphery. Pool admins who want allowlisted users to be able to use the router must allowlist it, which is the natural operational path. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle` on the router.

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **economic actor** (the end-user), not the intermediary contract. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should accept a `swapper` parameter (defaulting to `msg.sender`) and forward it as `callbackData`; the pool's swap callback can then re-expose it. Alternatively, the router can store the originating user in transient storage (analogous to how it already stores the payer) and expose it via a known interface that extensions can query.

2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should check the `sender` argument only when it is a known non-intermediary, or accept an authenticated user address from `extensionData` that the router populates and signs.

The simplest safe fix is for the router to store `msg.sender` in transient storage before calling the pool and expose it through a `IMetricOmmSimpleRouter.currentSwapper()` view; extensions that need the real user can call back into the router to retrieve it.

### Proof of Concept

```
Setup:
  1. Pool admin deploys pool with SwapAllowlistExtension.
  2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  3. Admin calls setAllowedToSwap(pool, router, true)  // required for alice to use the router

Attack (bob is not allowlisted):
  4. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: curated_pool,
         recipient: bob,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  5. Router calls pool.swap(bob, true, X, ...)
     → pool.msg.sender = router
     → _beforeSwap(sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router] == true  ✓  (no revert)
  6. Swap executes. bob receives output tokens.
     The allowlist check never evaluated bob's address.
```

The allowlist is fully bypassed. Bob trades in a pool he was never authorized to access.

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
