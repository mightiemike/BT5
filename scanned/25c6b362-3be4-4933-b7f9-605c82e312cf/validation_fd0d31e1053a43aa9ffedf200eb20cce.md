### Title
SwapAllowlistExtension Checks Router Address as `sender` Instead of Actual User, Enabling Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension receives the router address as `sender` — not the actual user. If the pool admin allowlists the router to enable router-mediated swaps, every user bypasses the allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist by checking the `sender` parameter: [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The pool dispatches `_beforeSwap(sender, recipient, ...)` where `sender` is the pool's own `msg.sender` — the direct caller of `pool.swap`. [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly, passing `params.recipient` as the first argument and becoming the pool's `msg.sender`: [3](#0-2) 

```solidity
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

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

Because the router is the direct caller of `pool.swap`, the pool sets `sender = address(router)` and passes it to the extension. The extension then evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`.

The allowlist is keyed and managed per pool and per swapper address: [5](#0-4) 

There is no mechanism for the router to forward the originating user's identity to the pool or extension.

---

### Impact Explanation

**Bypass path (High):** A pool admin configures `SwapAllowlistExtension` to restrict swaps to a curated set of addresses. To allow those users to also swap through the router (the standard periphery path), the admin must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] = true`, and every call through the router passes the check regardless of who the actual user is. Any non-allowlisted address can bypass the curated restriction by routing through `MetricOmmSimpleRouter`.

**Broken functionality path (Medium):** If the admin does not allowlist the router, allowlisted users cannot swap through the router at all — the extension reverts with `NotAllowedToSwap` because `sender = router` is not in the allowlist. This breaks the core swap flow for the intended users on any pool that uses `SwapAllowlistExtension`.

Both outcomes represent a broken invariant: the allowlist either fails to exclude unauthorized users or fails to include authorized ones, depending on how the admin configures it.

---

### Likelihood Explanation

`SwapAllowlistExtension` is a production periphery contract intended for curated pools. `MetricOmmSimpleRouter` is the standard user-facing swap entrypoint. Any pool that deploys both will encounter this mismatch on the first router-mediated swap. The trigger requires no privileged action beyond the normal pool setup; any user can call the router.

---

### Recommendation

The pool should pass the originating caller's identity separately from the direct `msg.sender`. One approach: add an explicit `swapper` parameter to `pool.swap` that the router populates with `msg.sender` (the actual user), and have the pool forward that value as `sender` to the extension. Alternatively, `SwapAllowlistExtension` can be redesigned to check `recipient` when `sender` is a known router, but this requires a registry of trusted routers and is more fragile. The cleanest fix is to thread the real user address through the call stack so the extension always sees the economically relevant actor.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as `extension1` in the `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** allowlist `charlie` (`setAllowedToSwap(pool, charlie, false)` by default).
4. `charlie` calls `router.exactInputSingle(...)` with `recipient = charlie`.
5. The router calls `pool.swap(charlie, ...)` — pool's `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, charlie, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. `charlie`'s swap executes despite never being allowlisted.

The same flow applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in `MetricOmmSimpleRouter`. [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-29)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
