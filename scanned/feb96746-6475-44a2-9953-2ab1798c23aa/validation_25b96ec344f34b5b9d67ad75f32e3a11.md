### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any user to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every user — including those not on the allowlist — can bypass the guard by routing through the router.

---

### Finding Description

**Call chain when a user swaps through the router:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ..., extensionData)   // msg.sender = router
     → _beforeSwap(sender = router, ...)
     → SwapAllowlistExtension.beforeSwap(sender = router, msg.sender = pool)
     → checks allowedSwapper[pool][router]         // NOT allowedSwapper[pool][user]
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original user's identity: [4](#0-3) 

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (to let their approved users trade via the standard periphery) inadvertently opens the pool to **all** users. Any address can call `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool and the extension will pass because it checks `allowedSwapper[pool][router]`, which is `true`. The allowlist is completely bypassed. Unauthorized traders can execute swaps against a pool that was intended to be restricted, draining liquidity at oracle prices that the pool admin only intended to expose to vetted counterparties.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point. Any user who discovers the pool is allowlist-gated will naturally try the router as an alternative path. No special privileges, flash loans, or multi-transaction setup are required — a single `exactInputSingle` call suffices. The pool admin cannot prevent this without removing the router from the allowlist entirely, which breaks the intended UX for legitimate users.

---

### Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediate router. Two viable approaches:

1. **Pass original caller in `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool, and the extension decodes and verifies it. This requires a coordinated convention between router and extension.

2. **Check `sender` against a router registry and then verify the payer stored in transient storage**: The router already stores the payer in transient storage (`_getPayer()`). The extension could call back into the router to retrieve the true payer, though this couples the extension to the router.

The simplest safe default is to document that `SwapAllowlistExtension` only gates direct pool callers and is **not** compatible with router-mediated swaps unless the pool admin explicitly understands that allowlisting the router opens the pool to all users.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps for their approved users.
3. Pool admin calls `setAllowedToSwap(pool, alice, true)` to allowlist Alice.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The pool calls `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
6. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. Bob's swap executes successfully despite not being on the allowlist.

The guard that was supposed to restrict swaps to Alice (and other approved addresses) is silently bypassed by any user routing through the public router. [6](#0-5) [7](#0-6)

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
