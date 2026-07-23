### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the actual user. A pool admin who allowlists the router to support router-based swaps for approved users inadvertently opens the gate to every user, because any caller can route through the router and pass the check.

---

### Finding Description

**Hook receives the wrong actor.**

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**Router substitutes its own address for the user.**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making `msg.sender` of that call the router contract, not the end user: [4](#0-3) 

The actual user (`msg.sender` of `exactInputSingle`) is stored only in the transient callback context for payment purposes and is never forwarded to the pool or the extension.

**Consequence of the mismatch.**

A pool admin who wants allowlisted users to be able to swap through the router must allowlist the router address itself (`allowedSwapper[pool][router] = true`). Once that entry exists, the `beforeSwap` check passes for every caller regardless of whether the actual end user is on the allowlist, because the hook only sees `sender = router`. [5](#0-4) 

---

### Impact Explanation

Any user who is not on the swap allowlist can bypass the curation policy of a pool by calling `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput` / `exactOutput`) instead of calling `pool.swap()` directly. The pool settles the swap normally — tokens are transferred, bin state is updated, fees are charged — so the bypass produces a real, settled trade on a pool that was supposed to be restricted. This breaks the core allowlist invariant and constitutes a direct policy bypass with fund-impacting consequences (non-approved counterparties trade against LP capital).

---

### Likelihood Explanation

The bypass requires the router to be allowlisted for the target pool. This is the natural configuration whenever a pool admin wants to support router-based swaps for approved users: the admin allowlists the router, intending it to act as a trusted intermediary. Because the router is a public, permissionless contract, allowlisting it is equivalent to opening the pool to all users. The trigger is a single `setAllowedToSwap(pool, router, true)` call by the pool admin, after which any unprivileged user can exploit the bypass immediately.

---

### Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor — the end user — not the intermediary contract. Two complementary fixes:

1. **Router-side:** `MetricOmmSimpleRouter` should forward the original `msg.sender` to the pool via `extensionData` (or a dedicated field), so extensions can recover the true initiator.
2. **Extension-side:** `SwapAllowlistExtension.beforeSwap()` should check the `sender` argument only when it is not a known router, or accept a signed/encoded user identity from `extensionData` when the direct caller is a trusted router.

The simplest correct fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode and check that value when the direct `sender` is a recognized router address.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   (alice is approved)
  allowedSwapper[pool][router] = true   (admin enables router-based swaps for alice)

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap(bob, ...)          // msg.sender = router
      → _beforeSwap(router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (check passes)
      → swap executes, bob receives tokens

Result:
  bob, who is not on the allowlist, completes a swap on a curated pool.
  The allowlist invariant is broken; LP capital is exposed to unapproved counterparties.
``` [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
