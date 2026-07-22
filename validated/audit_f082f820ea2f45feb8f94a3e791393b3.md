### Title
`SwapAllowlistExtension` checks the router address as the swapper identity, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument passed by the pool — which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap()` caller is the router contract, not the end user. If the router is allowlisted (or the pool admin adds it to allow router-based swaps), every user — including those not individually allowlisted — can bypass the curated pool's swap gate by routing through the router.

---

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to the extension's `beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then uses that `sender` value to look up the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the extension receives `sender = address(router)` and evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`. The actual end user's identity is never checked.

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all of which call `pool.swap()` from the router's address. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` as a `beforeSwap` hook intends to restrict swaps to a curated set of addresses. Once the pool admin allowlists the router (a necessary step for any user to swap via the standard periphery), the allowlist is effectively nullified: every user who calls through the router is seen as the router by the extension, and the router is allowlisted. Non-allowlisted users can execute live swaps on a curated pool, directly violating the pool's curation policy. This constitutes a broken core pool functionality and an admin-boundary break via an unprivileged path.

---

### Likelihood Explanation

The trigger requires only that the pool admin has allowlisted the router — a routine operational step for any pool that intends to support the standard periphery. Any user can then call `exactInputSingle` or any other router entry point. No special privilege, flash loan, or multi-step setup is needed. The attacker simply calls the router with a valid swap.

---

### Recommendation

The extension must gate on the actual end user, not the direct pool caller. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to supply honest data.
2. **Check `sender` (the router) and require the router to enforce its own per-user allowlist**: The extension allowlists the router as a trusted intermediary and relies on the router to enforce user-level restrictions — but this moves the trust boundary to the router, which currently has no such enforcement.
3. **Enforce the allowlist at the pool level, not the extension level**: Move the allowlist check into the pool's core `swap()` path so it always sees the true `msg.sender`, regardless of the extension system.

The cleanest fix is option 3 or a signed-permit pattern where the end user's identity is cryptographically bound to the swap call.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        recipient: attacker,
        ...
    })
  - Router calls pool.swap(recipient=attacker, ...)
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true  ✓
  - Swap executes; attacker is not individually allowlisted but swaps successfully.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
```

The root cause is at: [6](#0-5) 

where `sender` is the router address, not the end user, making the per-user allowlist check meaningless for any router-mediated swap.

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
