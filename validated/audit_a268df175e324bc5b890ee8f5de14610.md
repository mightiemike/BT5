### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` to `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router address**, not the user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. This makes the allowlist either universally bypassable (if the router is allowlisted) or universally broken for router users (if it is not).

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, amount, ..., extensionData)
              [msg.sender = router]
         → _beforeSwap(msg.sender=router, recipient, ...)
         → ExtensionCalling._callExtensionsInOrder(...)
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              checks allowedSwapper[pool][router]
```

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router address when the user entered through the router: [3](#0-2) 

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly with no forwarding of the original `msg.sender`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Two broken states result:**

1. **Pool admin allowlists the router** (so that allowlisted users can use the router): every user on the network can now call `exactInputSingle` through the router and pass the allowlist check, because the check resolves to `allowedSwapper[pool][router] == true`.

2. **Pool admin does not allowlist the router**: allowlisted users cannot use the router at all, even though they are individually permitted. The allowlist is broken in the opposite direction.

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users from doing so.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd, whitelisted, or otherwise vetted addresses is fully bypassable by any unprivileged user routing through `MetricOmmSimpleRouter`. The user receives pool output tokens at oracle-derived prices, and the pool's LP positions absorb the trade as if it were a permitted swap. This is a direct loss of the curation guarantee and constitutes broken core pool functionality for allowlisted pools. Any pool relying on this extension for regulatory compliance, LP protection, or access control is rendered insecure.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical, factory-verified periphery router. It is the expected entry point for most users. Any user who discovers the bypass can exploit it immediately with no special privileges, no malicious setup, and no non-standard tokens. The bypass is reachable on every allowlisted pool that has not explicitly set `allowAllSwappers = true` (which would make the allowlist pointless anyway).

---

### Recommendation

The extension must gate the **original user**, not the intermediary. Two approaches:

1. **Pass the original caller through the router**: modify `MetricOmmSimpleRouter` to forward `msg.sender` as `callbackData` or as a dedicated field, and have the pool or extension recover it. This requires a protocol-level convention.

2. **Check `sender` as the economic actor in the extension, and require the router to attest the real user**: the router could encode the real user in `extensionData`, and the extension could decode and verify it — but this is forgeable unless the pool enforces it.

3. **Simplest correct fix**: change `SwapAllowlistExtension.beforeSwap` to check the `recipient` field instead of `sender` when the sender is a known router, or require direct pool interaction for allowlisted pools (document that the router is incompatible with `SwapAllowlistExtension`).

The cleanest architectural fix is for the pool to pass the original initiating address (recoverable from the router's transient callback context) as a separate hook argument, so extensions can always gate the true economic actor.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can use the router (or the admin allowlists the router believing it is safe).
3. Attacker (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, tokenIn, tokenOut, amount, ...)`.
4. Router calls `pool.swap(recipient=attacker, ...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router] == true` → passes.
7. Attacker receives output tokens. The allowlist was never consulted for the attacker's own address.

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
