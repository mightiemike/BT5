### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the actual user's address. If the pool admin allowlists the router to enable router-based swaps, every user—including those not individually allowlisted—can bypass the per-user restriction by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user swaps through `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the allowlist check becomes `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. The pool admin faces an impossible choice:

- **Allowlist individual users only** → those users are blocked from using the router (because `sender` = router, which is not allowlisted).
- **Allowlist the router** → every user on the network can swap through the router, completely bypassing the per-user restriction.

There is no configuration that simultaneously allows specific users to swap and allows those users to use the router.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router address (a natural step to enable router-based UX) inadvertently opens the pool to all swappers. Any unprivileged address can call `router.exactInputSingle(...)` targeting the curated pool and the extension will pass because `sender` = router is allowlisted. Unauthorized traders can drain LP assets through arbitrage or execute trades the pool was explicitly configured to block. This is a direct loss of LP principal and a broken core pool invariant (the allowlist).

---

### Likelihood Explanation

Medium-to-high. The `MetricOmmSimpleRouter` is the canonical production swap interface. A pool admin who wants allowlisted users to enjoy router UX (slippage protection, multi-hop, deadline checks) will naturally allowlist the router address. The documentation and extension interface give no indication that doing so collapses the per-user gate to a per-router gate. The misconfiguration is easy to make and hard to detect without auditing the extension's actor-binding logic.

---

### Recommendation

The extension must check the **economic actor** (the end user), not the **transport actor** (the router). Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. The pool must enforce that `extensionData` cannot be spoofed (e.g., by requiring the pool itself to sign or forward a trusted payer field).

2. **Check `sender` AND require the router to forward the real user**: Add a `recipient`-style `swapper` field to the pool's `swap` signature that the router populates with `msg.sender`, and have the extension check that field instead of `sender`.

Until fixed, pool admins must be warned that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`.

---

### Proof of Concept

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin also allowlists the router to let Alice use it: `setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: curatedPool, ...})`.
5. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — `msg.sender` at the pool = router.
6. Pool calls `_beforeSwap(router, recipient, ...)` → extension receives `sender = router`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps in the curated pool despite never being allowlisted, bypassing the guard entirely. [3](#0-2) [5](#0-4) [1](#0-0)

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
