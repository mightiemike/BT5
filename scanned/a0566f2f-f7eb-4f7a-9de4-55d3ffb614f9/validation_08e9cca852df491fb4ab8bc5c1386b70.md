Looking at the `SwapAllowlistExtension.beforeSwap` and how the pool passes `sender` through the extension calling chain, and comparing it to the `DepositAllowlistExtension` which correctly gates on `owner`, I can identify a concrete wrong-actor binding analog.

---

### Title
`SwapAllowlistExtension` gates on the direct pool caller (`sender = router`) instead of the end-user, making the allowlist unenforceable for router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the direct caller of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. This produces two fund-impacting outcomes: (1) allowlisted users on curated pools cannot use the router at all, breaking core swap flows; and (2) if the pool admin allowlists the router to restore router access for allowlisted users, every unprivileged user can bypass the per-user allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(recipient, ...)` with `msg.sender = router`: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual end-user's identity is never checked.

Contrast this with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly gates on `owner` (the position owner, not the caller): [5](#0-4) 

The pool passes `owner` (the intended economic actor) to the deposit hook, but passes `msg.sender` (the intermediary) to the swap hook. The swap hook therefore checks the wrong identity.

---

### Impact Explanation

**Path A — Broken core swap flow (no privileged action required):** Any allowlisted user on a curated pool who attempts to swap through the router (e.g., to use native ETH via `unwrapWETH9`, to do a multi-hop `exactInput`, or to use `exactOutputSingle`) will have their swap reverted with `NotAllowedToSwap` because the router is not in the allowlist. The user's funds are not locked in the pool, but the router-mediated swap path is completely unusable for every allowlisted user on every curated pool — a broken core pool functionality impact.

**Path B — Complete allowlist bypass (semi-trusted trigger):** A pool admin who wants to restore router access for their allowlisted users has no correct way to do so other than calling `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every call that arrives through the router, regardless of who the actual user is. Any unprivile

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

**File:** metric-core/contracts/ExtensionCalling.sol (L91-99)
```text
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
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
