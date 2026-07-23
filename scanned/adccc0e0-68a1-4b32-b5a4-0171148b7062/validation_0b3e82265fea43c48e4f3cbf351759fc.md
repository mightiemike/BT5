### Title
`SwapAllowlistExtension` checks router address as swapper instead of original user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool always sets to `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` is the direct caller, `sender` is the router address, not the original user. This produces two fund-impacting outcomes: (1) allowlisted users are silently blocked from using the standard periphery, and (2) if the pool admin adds the router to the allowlist as a workaround, every unprivileged user can bypass the per-user allowlist entirely.

---

### Finding Description

**Root cause — wrong actor bound in `beforeSwap`**

`MetricOmmPool.swap` always passes `msg.sender` as the `sender` argument to the extension dispatcher: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first parameter — the direct caller of `pool.swap()`: [3](#0-2) 

**Router path — sender is always the router, never the user**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly; `msg.sender` seen by the pool is the router: [4](#0-3) 

For multi-hop `exactInput`, every hop is called from the router, so `sender = router` for all pools in the path: [5](#0-4) 

For `exactOutput`, intermediate hops are triggered inside `_exactOutputIterateCallback`, which is still executed on the router contract, so `msg.sender` of each subsequent `pool.swap()` call is again the router: [6](#0-5) 

In every router-mediated path the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`.

---

### Impact Explanation

**Path A — false block (broken core swap flow):**
A pool admin allowlists Alice and Bob as the only permitted swappers. Alice calls `router.exactInputSingle(pool, ...)`. The extension checks `allowedSwapper[pool][router]` → `false` → `NotAllowedToSwap`. Alice is blocked from using the standard periphery even though she is explicitly allowlisted. The only workaround is to call `pool.swap()` directly, bypassing the router's slippage protection, deadline enforcement, and multi-hop composition.

**Path B — allowlist bypass (curation failure):**
The pool admin, observing that allowlisted users cannot use the router, adds the router address to the allowlist (`setAllowedToSwap(pool, router, true)`). Now `allowedSwapper[pool][router] = true`. Any unprivileged user — including addresses the admin explicitly never allowlisted — can call `router.exactInputSingle(pool, ...)` and the check passes. The per-user allowlist is completely defeated. On a curated pool where the oracle price is

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
