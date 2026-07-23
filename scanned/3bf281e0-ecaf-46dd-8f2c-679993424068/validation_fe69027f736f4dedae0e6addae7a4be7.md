### Title
SwapAllowlistExtension checks router address instead of actual user, allowing full allowlist bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` inside `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` in the pool is the **router contract**, not the original user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the per-user restriction entirely.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks whether that `sender` is allowlisted for the calling pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router itself `msg.sender` inside the pool — the original user's address is never forwarded: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

Because the extension sees the router address as `sender`, the allowlist check is `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. Two broken outcomes follow:

**Outcome A — Bypass:** The pool admin adds the router to `allowedSwapper` (a natural step when trying to enable router-mediated swaps for allowlisted users). Every unprivileged user can now swap through the router unchecked, defeating the entire access-control purpose of the extension.

**Outcome B — Broken core flow:** The pool admin adds only specific users to `allowedSwapper` but not the router. Those allowlisted users cannot use the standard router at all; every router-mediated swap reverts with `NotAllowedToSwap`. The only workaround is calling `pool.swap()` directly, which requires implementing the `IMetricOmmSwapCallback` interface — not a realistic option for ordinary EOAs.

### Impact Explanation

Under Outcome A, any unprivileged address can trade in a pool that was explicitly restricted to a curated set of counterparties. Depending on pool configuration, this enables unauthorized extraction of LP value through oracle-driven arbitrage or price manipulation that the allowlist was meant to prevent. Under Outcome B, the standard swap path is broken for all allowlisted users, making the pool's liquidity inaccessible through the production periphery.

### Likelihood Explanation

The pool admin must configure the extension. A natural and documented use-case is "allow only approved traders." When those traders report that router swaps fail, the admin's first corrective action is to add the router to `allowedSwapper` — which immediately opens Outcome A. The trigger is a single, well-motivated admin action with no on-chain safeguard preventing it.

### Recommendation

`SwapAllowlistExtension` should gate on the **original user**, not the direct pool caller. Two approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change in the router and extension.
2. **Check `sender` only when it is not a known router:** Maintain a registry of trusted routers; when `sender` is a router, decode the real user from `extensionData`.

The simplest safe fix is to document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and provide a router-aware variant that accepts and verifies a signed user identity in `extensionData`.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the intended gated user
  allowedSwapper[pool][router] = true         // admin adds router to unblock alice's router swaps

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...)          // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
        → swap executes, bob receives tokens

Result: bob bypasses the allowlist entirely because the extension
        checked the router address, not bob's address.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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
