### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore checks whether the **router** is allowlisted, not the individual user. This makes the allowlist either permanently broken for all router users, or trivially bypassable by any user if the router is added to the allowlist.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
              → MetricOmmPool._beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[msg.sender/*pool*/][sender/*router*/]  ← WRONG identity
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then uses that `sender` argument — which is the router — to look up the allowlist: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making itself `msg.sender` from the pool's perspective. It never forwards the originating user address: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

Two fund-impacting outcomes arise from the same root cause:

**Outcome A — Allowlist bypass (Critical):** A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, *any* address — including non-allowlisted users — can call `exactInputSingle` and the extension passes, because the check resolves to `allowedSwapper[pool][router] == true`. The entire per-user gate is defeated. Any user can drain pool liquidity at oracle price, bypassing the KYC/access control the pool was configured to enforce.

**Outcome B — Broken swap functionality (High):** If the pool admin does *not* allowlist the router, then even explicitly allowlisted users cannot swap through the router. Their individual allowlist entries (`allowedSwapper[pool][user] = true`) are never consulted; the extension sees `sender = router` and reverts `NotAllowedToSwap`. The primary user-facing swap interface is permanently unusable for all users of that pool.

Both outcomes are reachable by any unprivileged user once a pool is deployed with `SwapAllowlistExtension` and the `beforeSwap` hook enabled.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the documented primary swap interface. Any pool deployer who configures `SwapAllowlistExtension` and expects users to interact via the router will immediately encounter one of the two failure modes. The bypass path (Outcome A) requires only that the admin adds the router to the allowlist — a natural operational step to unblock router users — after which the allowlist is silently defeated for all users.

---

### Recommendation

The extension must check the **originating user**, not the intermediary. Two complementary fixes:

1. **In `SwapAllowlistExtension.beforeSwap`**: use the `sender` argument as the identity to check (it already is), but the pool must pass the correct value. Alternatively, expose a separate `isAllowedToSwap(pool, sender)` view and have the pool call it with the correct actor.

2. **In `MetricOmmPool.swap`** (preferred, protocol-level fix): accept an explicit `sender` parameter from the caller (analogous to how `addLiquidity` accepts an explicit `owner`), or document that `sender` passed to extensions is always `msg.sender` of the pool and that extensions must not use it for per-user gating when a router is in the path.

3. **In `MetricOmmSimpleRouter`**: pass `msg.sender` (the originating user) as a field in `extensionData` so that extensions can decode and verify the real actor — though this requires extension-side cooperation and is less robust.

The cleanest fix mirrors the `addLiquidity` pattern: `addLiquidity` accepts an explicit `owner` that the pool passes to extensions, and the `DepositAllowlistExtension` correctly gates on `owner`. `swap` should accept an explicit `swapper` parameter that the router populates with `msg.sender`. [6](#0-5) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension, beforeSwap hook enabled
  - allowedSwapper[pool][alice] = true   (alice is KYC'd)
  - allowedSwapper[pool][bob]   = false  (bob is not KYC'd)
  - allowedSwapper[pool][router] = true  (admin adds router to unblock alice)

Attack (Outcome A — bypass):
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → pool.swap(recipient, ...) with msg.sender = router
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  → PASSES
  bob successfully swaps on an allowlisted pool he was never permitted to access.

Broken functionality (Outcome B — if router not allowlisted):
  alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → pool.swap(recipient, ...) with msg.sender = router
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == false → REVERTS NotAllowedToSwap
  alice cannot use the router despite being explicitly allowlisted.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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
