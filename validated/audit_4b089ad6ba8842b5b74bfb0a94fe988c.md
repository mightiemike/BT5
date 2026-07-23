### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the per-pool swap allowlist via the router — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of `MetricOmmPool.swap`. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` of the pool, so the extension checks the **router address** against the allowlist instead of the **actual user**. A pool admin who allowlists the router to enable normal router-mediated swaps inadvertently opens the pool to every user, defeating the entire per-user access control.

---

### Finding Description

**Identity propagation chain:**

`MetricOmmPool.swap` captures `msg.sender` and forwards it as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` as the first argument of the hook call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap(...)`, the pool's `msg.sender` is the **router contract**, not the end user: [4](#0-3) 

The allowlist lookup therefore becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

---

### Impact Explanation

**Medium — broken access-control gate with direct swap-access consequence.**

A pool admin who deploys a `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC-verified traders, institutional counterparties). To let those users interact through the standard router UX, the admin must call `setAllowedToSwap(pool, router, true)`. The moment the router is allowlisted, the guard collapses: every public user can call `router.exactInputSingle` and the extension returns success because `allowedSwapper[pool][router] == true`. The pool's restricted-access invariant is permanently broken for all router-mediated swaps, which is the primary swap path for end users.

Conversely, if the admin does **not** allowlist the router and only allowlists individual users, those users cannot use the router at all — their swaps revert because the extension sees the router address and finds it unlisted. Either outcome breaks the intended guard.

---

### Likelihood Explanation

**High.** The router is the standard, documented swap entry point. Any pool admin who wants to support normal UX will allowlist the router. Once that is done, any unprivileged user can bypass the allowlist with a single `exactInputSingle` call. No special knowledge, flash loans, or multi-step setup is required.

---

### Recommendation

The extension must resolve the **economic actor** (the end user), not the **call-chain intermediary** (the router). Two viable approaches:

1. **Trusted-forwarder pattern in the router:** Have the router ABI-encode `msg.sender` into `extensionData` and have the extension decode and verify it, accepting only calls where `msg.sender` is a known factory-registered router.
2. **Pool-level user forwarding:** Add an optional `originalSender` field to the swap call that the pool passes through to extensions, populated by the router with its own `msg.sender`.

Until one of these is implemented, the `SwapAllowlistExtension` cannot simultaneously allow router-mediated swaps and enforce per-user access control.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls:
       swapExtension.setAllowedToSwap(pool, address(router), true);
   (necessary to let any allowlisted user swap via the router)
3. Attacker (not individually allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams({
           pool: pool,
           recipient: attacker,
           tokenIn: token0,
           amountIn: 1e18,
           ...
       }));
4. Call stack:
       router.exactInputSingle()
         → pool.swap(recipient, ...) [msg.sender = router]
           → _beforeSwap(sender=router, ...)
             → SwapAllowlistExtension.beforeSwap(sender=router, ...)
               → allowedSwapper[pool][router] == true  ← passes
5. Attacker's swap executes on the restricted pool.
   The per-user allowlist is completely bypassed.
``` [3](#0-2) [5](#0-4) [6](#0-5)

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
