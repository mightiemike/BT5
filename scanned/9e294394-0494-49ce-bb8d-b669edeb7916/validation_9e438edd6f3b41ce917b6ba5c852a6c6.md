### Title
`SwapAllowlistExtension` gates on the router's address instead of the originating user — any user bypasses the allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`MetricOmmPool.swap()` passes `msg.sender` (the router contract) as the `sender` argument to every `beforeSwap` extension hook. `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[pool][sender]`, so it evaluates the **router's identity**, not the originating user's identity. If the pool admin allowlists the router to enable router-mediated swaps for their intended users, every unprivileged user can call `MetricOmmSimpleRouter` and pass the allowlist check, completely bypassing the intended access control.

---

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`_beforeSwap` forwards that value verbatim as `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, not the end user: [4](#0-3) 

The allowlist therefore evaluates `allowedSwapper[pool][router]`. A pool admin who wants their allowlisted users to be able to use the router must allowlist the router address. Once the router is allowlisted, **every** caller of the router — including addresses the admin never intended to permit — passes the `beforeSwap` guard.

For multi-hop `exactInput`, intermediate hops use `address(this)` (the router) as payer, so the router is always the `sender` seen by every intermediate pool's extension: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or whitelisted market makers) is fully open to any user who routes through `MetricOmmSimpleRouter`. The attacker receives the same swap execution — at oracle-derived prices — as an allowlisted user, draining pool liquidity or extracting value that the pool admin intended to gate. This is a direct loss of the pool's intended access-control invariant with fund-impacting consequences (unauthorized swaps against LP capital).

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router. This is the natural operational step: a pool admin who deploys a restricted pool but still wants their allowlisted users to access it through the standard periphery router will allowlist the router address. The admin's mental model is "the router is a trusted intermediary for my users," but the extension has no way to distinguish which user invoked the router. The bypass is then available to any unprivileged address with no special setup.

---

### Recommendation

The `SwapAllowlistExtension` should gate on the **originating user**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` should forward `msg.sender` (the end user) as a verified `sender` field in `extensionData`, and `SwapAllowlistExtension` should decode and verify it. However, this requires the extension to trust the router, which reintroduces a trust assumption.

2. **Check `tx.origin` as a fallback** (not recommended for general use, but acceptable for allowlist-only extensions on non-contract users).

3. **Preferred**: The pool's `swap()` interface should accept an explicit `sender` parameter that the router fills with `msg.sender` (the end user), and the pool should pass that value — not its own `msg.sender` — to extension hooks. This mirrors how Uniswap v4 separates `sender` from `msg.sender` in hook calls.

Until fixed, pool admins should **not** allowlist the router address; instead, they must require allowlisted users to call the pool directly.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured. Admin allowlists `alice` and the `MetricOmmSimpleRouter` (so `alice` can use the router).
2. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: restrictedPool, ...})`.
3. The router calls `pool.swap(...)`. The pool's `msg.sender` is the router.
4. `_beforeSwap` is called with `sender = router`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
6. The swap executes. `bob` receives output tokens from the restricted pool without ever being allowlisted.

The allowlist is completely bypassed. The only guard that would prevent this is if the admin never allowlists the router — but then no user can use the router at all, breaking the intended UX. [3](#0-2) [1](#0-0) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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
