### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

When `MetricOmmSimpleRouter` is used to swap on a pool protected by `SwapAllowlistExtension`, the extension receives the **router's address** as `sender` instead of the actual user's address. If the pool admin allowlists the router (which is required for any router-based swap to succeed), every user — including those explicitly excluded from the allowlist — can bypass the per-user gate by routing through the router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` value into the call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` = pool): [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router itself calls `pool.swap(...)`: [4](#0-3) 

At that point `msg.sender` inside `MetricOmmPool.swap` is the **router contract**, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

### Impact Explanation

A pool admin who configures `SwapAllowlistExtension` intends to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers). For router-based swaps to work at all, the admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **any** address — including those the admin explicitly excluded — can call any router entry-point and the extension will approve the swap because it only sees the router. The allowlist is completely neutralised on the router path, which is the primary user-facing entry point. Disallowed users gain full swap access to a curated pool, defeating the curation policy and any compliance or risk controls it encodes.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical periphery swap interface. Any pool admin who wants router-based swaps to function must allowlist the router, making the bypass condition trivially satisfied. No special knowledge or privileged access is required; any user who knows the router address can exploit this.

### Recommendation

The extension must be able to identify the **originating user**, not the immediate `msg.sender` of `pool.swap`. Two sound approaches:

1. **Pass the originating user explicitly.** Add a `swapper` field to the `extensionData` payload that the router populates with `msg.sender` before calling the pool. The extension reads and verifies this field instead of (or in addition to) the `sender` parameter. The pool's reentrancy guard already prevents the router from being re-entered, so the field cannot be spoofed within a single transaction.

2. **Check the router's stored payer.** The router already stores the originating payer in transient storage (`_setNextCallbackContext(..., msg.sender, ...)`). Expose a view on the router that returns the current payer, and have the extension query it when `sender` is a known router address.

Until fixed, pools that require per-user swap gating must not allowlist the router and must instruct users to call `pool.swap` directly.

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension as extension1, beforeSwap order = 1.
2. Pool admin calls setAllowedToSwap(pool, router, true)   // enable router path
3. Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the only intended user
4. bob is NOT allowlisted.

Attack
──────
5. bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool:          pool,
       recipient:     bob,
       zeroForOne:    true,
       amountIn:      X,
       extensionData: ""
   })

Trace
─────
6. Router calls pool.swap(bob, true, X, limit, "", "")
   → msg.sender inside pool.swap = router
7. pool._beforeSwap(sender=router, ...)
8. SwapAllowlistExtension.beforeSwap(sender=router, ...)
   → allowedSwapper[pool][router] == true  ✓
   → swap proceeds
9. bob receives output tokens despite never being allowlisted.
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
