### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is always `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks whether the **router** is allowlisted rather than the **original user**. A pool admin who allowlists the router to support router-mediated swaps for their curated users inadvertently opens the allowlist to every user on the network.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

In every `MetricOmmSimpleRouter` swap path — `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` — the router itself calls `pool.swap()`: [4](#0-3) [5](#0-4) 

Therefore `sender` delivered to the extension is always the router address, never the originating EOA. The extension cannot distinguish between an allowlisted user routing through the router and a completely non-allowlisted user doing the same.

### Impact Explanation

A pool admin who wants to support router-mediated swaps for their allowlisted users must add the router to `allowedSwapper[pool][router]`. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call that arrives through the router, regardless of who initiated the transaction. Any non-allowlisted user can call `exactInputSingle` or any other router entry point and the extension will pass, completely defeating the curated-pool policy. The attacker receives real token output from the pool at oracle-anchored prices; the pool's LP assets are exposed to unrestricted trading that the allowlist was meant to prevent.

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router. This is the natural action for any admin who wants their allowlisted users to be able to use the standard periphery. The `MetricOmmSimpleRouter` is the documented, supported swap entry point. There is no warning in the extension or its interface that allowlisting the router grants universal access. The bypass is therefore reachable on any curated pool that supports router-mediated swaps.

### Recommendation

The extension must recover the original transaction initiator rather than trusting the `sender` argument forwarded by the pool. Two sound approaches:

1. **Pass `tx.origin` as an additional parameter** in the extension interface so the pool can forward the true initiator. This requires an interface change.
2. **Check both the direct caller and `tx.origin`** inside the extension: gate on `tx.origin` when `sender` is a known router, or require that `sender == tx.origin` for non-router callers.
3. **Document clearly** that allowlisting the router grants access to all router users, and require pool admins to use `allowAllSwappers` instead if that is the intent.

The cleanest fix is to add an `originator` field to the `beforeSwap` hook arguments so the pool can pass `tx.origin` (or a router-recovered caller) alongside `msg.sender`.

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  allowedUser  = 0xAAAA  (KYC'd, allowlisted)
  attacker     = 0xBBBB  (not allowlisted)
  router       = MetricOmmSimpleRouter

Admin actions:
  swapExt.setAllowedToSwap(pool, allowedUser, true)   // allowlist the real user
  swapExt.setAllowedToSwap(pool, router,      true)   // allowlist router so allowedUser can use it

Attack:
  vm.prank(attacker);
  router.exactInputSingle(ExactInputSingleParams({
      pool:            pool,
      recipient:       attacker,
      zeroForOne:      true,
      amountIn:        1_000e18,
      amountOutMinimum: 0,
      priceLimitX64:   0,
      tokenIn:         token0,
      extensionData:   ""
  }));
  // router calls pool.swap() → msg.sender to pool = router
  // extension checks allowedSwapper[pool][router] == true → passes
  // attacker receives token1 output; allowlist is bypassed
``` [6](#0-5) [7](#0-6) [1](#0-0)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
