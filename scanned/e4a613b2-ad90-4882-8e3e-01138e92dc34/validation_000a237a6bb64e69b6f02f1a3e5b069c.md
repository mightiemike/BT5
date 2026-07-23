### Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. The pool always passes `msg.sender` (the immediate caller of `pool.swap`) as `sender`. When `MetricOmmSimpleRouter` is the caller, `sender` is the router's address, not the originating user. If the pool admin allowlists the router to support router-based swaps, every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap` with the router as `msg.sender`. [5](#0-4) 

The result: the allowlist lookup becomes `allowedSwapper[pool][router]`. If the pool admin allowlists the router (a natural step to enable router-based swaps for curated users), the check passes for **every** user who routes through it, regardless of whether that user's own address is in the allowlist.

The contract's own NatSpec states its purpose as "Gates `swap` by swapper address, per pool": [6](#0-5) 

That invariant is broken when the router is the intermediary.

---

### Impact Explanation

A curated pool that uses `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps, draining LP-owned token reserves at oracle prices. This is a direct loss of LP principal and a broken core pool invariant (curated access control).

---

### Likelihood Explanation

Medium. The pool admin must have allowlisted the router address. This is the expected operational step for any pool that wants to support the standard periphery router while also maintaining a curated allowlist. The admin has no on-chain signal that allowlisting the router collapses per-user gating — the `isAllowedToSwap` view function and the `setAllowedToSwap` setter both operate on individual addresses with no warning about router aggregation. [7](#0-6) 

---

### Recommendation

The extension must resolve the originating user, not the immediate caller. Two options:

1. **Pass the originating user through the router.** Add a `swapper` field to the router's `extensionData` payload and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router. This requires a trusted router registry or a signed payload.

2. **Check `sender` only when it is not a known intermediary, and require the router to forward the real user.** The cleaner fix is to have the pool (or the router) pass the originating `msg.sender` as a dedicated field in `extensionData`, and have the extension decode it. The pool's `sender` parameter should represent the economic actor, not the technical caller.

The `DepositAllowlistExtension` avoids this specific issue because it checks `owner` (an explicit argument the caller supplies), not `sender`: [8](#0-7) 

`SwapAllowlistExtension` should adopt the same pattern — gate on a user-supplied or callback-verified identity rather than the raw `msg.sender` chain.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, address(router), true)
    to enable router-based swaps.
  - Alice (address not in allowlist) calls:
      router.exactInputSingle({pool: pool, ..., recipient: alice, ...})
  - Router calls pool.swap(alice, ...) → msg.sender = router
  - _beforeSwap receives sender = router
  - Extension checks allowedSwapper[pool][router] → true
  - Swap executes; Alice receives output tokens.
  - Alice was never individually allowlisted.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
``` [9](#0-8) [10](#0-9)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-25)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
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
