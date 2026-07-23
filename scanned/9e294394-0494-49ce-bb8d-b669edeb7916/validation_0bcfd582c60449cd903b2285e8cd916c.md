### Title
Swap Allowlist Checks Router Address Instead of Actual User, Allowing Full Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router address**, not the original user. The allowlist therefore checks whether the router is allowlisted, not whether the actual user is allowlisted. Any pool admin who allowlists the router to let their approved users trade through it simultaneously opens the gate to every unprivileged user on the network.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded from the pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router**, so `sender` delivered to the extension is the router address. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

### Impact Explanation

A pool admin who wants their allowlisted users to be able to trade through the router must add the router to the allowlist. The moment they do, `allowedSwapper[pool][router] = true` and the check `!allowedSwapper[msg.sender][sender]` passes for **every caller** of the router, regardless of whether that caller is on the allowlist. The entire curation policy of the pool is silently voided. Any unprivileged user can drain LP value or execute swaps that the pool was explicitly configured to block.

Conversely, if the admin does not allowlist the router, their own approved users cannot use the router at all, breaking the intended user experience and forcing direct pool calls.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint for the protocol. Pool admins who configure `SwapAllowlistExtension` to restrict trading to a curated set of addresses will naturally also want those addresses to be able to use the router. Allowlisting the router is the only way to achieve that, and doing so is the direct trigger for the bypass. The attacker needs no special privilege: they simply call any `exact*` function on the router targeting the pool.

### Recommendation

The extension must gate on the **original user**, not the intermediary. Two complementary fixes:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` for each hop. `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of `sender`.

2. **Check both `sender` and a decoded user field**: If `sender` is a known factory pool or the router, fall back to the user address embedded in `extensionData`; otherwise check `sender` directly.

Either approach ensures the allowlist always gates the economically relevant actor, regardless of which supported periphery path reaches the pool.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only approved user
  allowedSwapper[pool][router] = true  // admin adds router so alice can use it

Attack (bob, not on allowlist):
  bob calls router.exactInputSingle({pool: pool, ...})
  router calls pool.swap(recipient, ...)   // msg.sender = router
  pool calls _beforeSwap(router, ...)
  extension checks allowedSwapper[pool][router] == true  → passes
  bob's swap executes on the curated pool
```

The allowlist is fully bypassed. Bob receives tokens from a pool that was supposed to be restricted to alice only.

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
