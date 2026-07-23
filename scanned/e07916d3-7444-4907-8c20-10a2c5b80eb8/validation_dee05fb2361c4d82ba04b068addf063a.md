### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any caller to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` equals the router's address, not the end user. A pool admin who allowlists the router to enable standard UX inadvertently opens the pool to every caller, defeating the per-user allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly — making the router the `msg.sender` seen by the pool, and therefore the `sender` seen by the extension: [4](#0-3) 

The same substitution occurs in `exactInput` (hop 0 payer is `msg.sender`, but the pool still sees the router as `msg.sender`) and in `exactOutputSingle`/`exactOutput`. [5](#0-4) 

**The structural trap:** A pool admin who wants allowlisted users to access the pool via the standard router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`, so the check passes for every caller regardless of their identity. The per-user allowlist is completely bypassed for any swap routed through `MetricOmmSimpleRouter`. [6](#0-5) 

---

### Impact Explanation

Any address can swap against a pool that the admin intended to restrict to a specific set of counterparties, simply by calling `MetricOmmSimpleRouter`. If the pool offers favorable oracle-anchored pricing (e.g., a private market-making arrangement), unauthorized traders can extract value from LP positions. The `SwapAllowlistExtension` is the only on-chain mechanism for per-user swap gating; its bypass removes the sole access-control layer protecting LP funds in restricted pools.

---

### Likelihood Explanation

The router is the canonical, documented swap entry point for end users. A pool admin who deploys a restricted pool and then wants allowlisted users to use the standard UX will naturally allowlist the router address. This is a foreseeable operational step, not an exotic misconfiguration. The bypass requires no special privilege — any EOA or contract can call `MetricOmmSimpleRouter`.

---

### Recommendation

Pass the economically relevant actor — the end user — rather than the immediate `msg.sender` of `pool.swap`. Two concrete options:

1. **Router-side**: Have `MetricOmmSimpleRouter` pass the original `msg.sender` as `callbackData` or a dedicated `swapper` field, and have the pool forward it as `sender` to extensions. This requires a protocol-level change to the swap interface.

2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should accept an explicit `swapper` address encoded in `extensionData` and verify it against the allowlist, with the router responsible for injecting `msg.sender` into that payload. The extension should reject calls where `extensionData` is empty or malformed.

Either approach must ensure the checked identity cannot be spoofed by the caller supplying arbitrary `extensionData`.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended user
3. Pool admin calls setAllowedToSwap(pool, router, true)      // needed so alice can use the router

Attack
──────
4. attacker (not in allowlist) calls:
     MetricOmmSimpleRouter.exactInputSingle({
       pool:          restrictedPool,
       recipient:     attacker,
       zeroForOne:    true,
       amountIn:      X,
       ...
     })

5. Router calls pool.swap(attacker, true, X, ...) — msg.sender = router
6. Pool calls _beforeSwap(router, attacker, ...)
7. Extension checks allowedSwapper[pool][router] == true  ✓
8. Swap executes; attacker receives output tokens.

Result: attacker bypasses the per-user allowlist and swaps against a pool
        that was supposed to be restricted to alice only.
``` [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
