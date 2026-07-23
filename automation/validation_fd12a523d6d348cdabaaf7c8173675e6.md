### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks the router's address against the allowlist instead of the actual user's address. A pool admin who allowlists the router to support router-based swaps inadvertently opens the pool to every user, defeating the allowlist entirely.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (used as the per-pool namespace key) and `sender` is the value received from the pool — i.e., whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

Inside the pool, `msg.sender` is the **router**, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual user's identity is never consulted.

The same wrong-actor binding applies to every router entry point (`exactInput`, `exactOutputSingle`, `exactOutput`) and to every hop in multi-hop paths: [5](#0-4) 

Note the contrast with `DepositAllowlistExtension`, which correctly checks the `owner` argument (the position owner, the economically relevant actor) rather than the immediate caller: [6](#0-5) 

### Impact Explanation

Two distinct failure modes arise:

**Bypass (High):** A pool admin who wants to support router-based swaps for allowlisted users must add the router to the allowlist. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and every user — including those explicitly not allowlisted — can swap through the router without restriction. The allowlist is completely defeated for all router-mediated swaps.

**Broken functionality (Medium):** If the admin does not allowlist the router, individually allowlisted users cannot use the router at all, even though they are permitted to swap. There is no configuration that simultaneously supports router-based swaps and per-user allowlisting.

In both cases the invariant stated in the protocol's own audit target — *"A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it"* — is broken.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool that deploys `SwapAllowlistExtension` and also expects users to swap through the router will encounter this issue. The admin action of allowlisting the router is a natural and expected step for any pool that wants to support router-based swaps, making the bypass scenario realistic without requiring any malicious setup.

### Recommendation

Pass the originating user's address through the call chain rather than `msg.sender`. Two concrete approaches:

1. **Preferred — explicit `sender` parameter on `pool.swap`:** Add a `sender` parameter to `IMetricOmmPoolActions.swap` that the pool forwards to extensions. The router passes `msg.sender` (the actual user) as this argument. The pool continues to use its own `msg.sender` for the swap callback (payment), keeping payment and identity separate.

2. **Alternative — extension reads transient storage:** The router writes the originating user into a transient slot before calling the pool; the extension reads that slot. This avoids changing the pool interface but couples the extension to the router's transient layout.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true).
  - Pool admin does NOT call setAllowedToSwap(pool, bob, true).
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to allow alice to use the router).

Attack:
  1. bob calls router.exactInputSingle({pool: pool, ...}).
  2. Router calls pool.swap(recipient, ...) — msg.sender in pool = router.
  3. Pool calls _beforeSwap(router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
  5. bob's swap executes successfully despite not being on the allowlist.

Result:
  bob, an explicitly non-allowlisted address, completes a swap on a curated pool
  by routing through the router, bypassing the SwapAllowlistExtension entirely.
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
