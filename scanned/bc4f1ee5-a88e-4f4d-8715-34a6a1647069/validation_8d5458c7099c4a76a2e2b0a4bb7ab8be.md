### Title
`SwapAllowlistExtension` Gates on Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract address**, not the actual user. If the router is allowlisted (which pool admins must do to allow any router-mediated swap), the allowlist gate is completely bypassed for every user, including those the pool admin explicitly intended to exclude.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` verbatim to every configured extension: [2](#0-1) 

The `SwapAllowlistExtension.beforeSwap` hook performs its allowlist lookup keyed on `(pool, sender)` — i.e., it checks whether the **immediate caller of the pool** is allowlisted, not the economic actor who initiated the swap.

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [3](#0-2) 

At this point `msg.sender` to the pool is `address(MetricOmmSimpleRouter)`, so `sender` passed to the hook is the router address — not `msg.sender` of the original `exactInputSingle` call (the actual user).

This creates an irreconcilable dilemma for pool admins:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert, even for allowlisted users — broken core functionality |
| Router **allowlisted** | Every user, including explicitly excluded ones, can bypass the allowlist by routing through the router |

The same problem applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` from the router's address: [4](#0-3) [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swapping to specific addresses (e.g., KYC-verified users, whitelisted market makers, or protocol-internal actors) provides **no effective restriction** once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` and execute swaps in a pool that was intended to be closed to them. This constitutes unauthorized access to pool liquidity, potential draining of LP assets at oracle-derived prices, and a complete failure of the pool's access-control invariant. Impact is **High** — direct loss of LP principal through unauthorized swaps in restricted pools.

---

### Likelihood Explanation

Likelihood is **Medium**. The `MetricOmmSimpleRouter` is the canonical user-facing swap interface. Any pool admin who deploys a `SwapAllowlistExtension` and also wants allowlisted users to be able to use the router (the normal UX path) must allowlist the router, at which point the bypass is immediately available to all users. The attacker requires no special privileges — only knowledge of the router address and the pool address.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **actual economic actor**, not the immediate pool caller. Two sound approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before forwarding to the pool. The extension decodes and checks this value. This requires the extension to trust the router as a relay, which can be enforced by also checking that `sender` is a known factory-registered router.

2. **Check `sender` only for direct calls; decode user from `extensionData` for router calls**: The extension inspects whether `sender` is a registered router; if so, it decodes the real user from `extensionData` and checks that address against the allowlist.

The analogous fix in the external report (ParaSpace) was to add a user-supplied `deadline` parameter so the constraint is bound to the actual actor's intent — here, the fix is to bind the allowlist check to the actual actor's identity rather than the intermediary contract's address.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` as `EXTENSION_1`, configured with `allowAll = false`.
2. Pool admin allowlists `alice` and also allowlists `address(router)` so that `alice` can swap via the router.
3. `bob` (not allowlisted) calls `router.exactInputSingle({pool: pool, ..., extensionData: ""})`.
4. The router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` with `msg.sender = address(router)`.
5. `_beforeSwap` is called with `sender = address(router)`.
6. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][address(router)]` → `true` (allowlisted in step 2).
7. The hook returns its selector without reverting.
8. `bob`'s swap executes successfully, bypassing the allowlist entirely.

The allowlist invariant — "only addresses explicitly permitted by the pool admin may swap" — is broken. `bob` receives output tokens from a pool that was configured to exclude him. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
