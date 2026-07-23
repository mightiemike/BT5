### Title
`SwapAllowlistExtension` checks router address as swapper identity, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is `msg.sender` of the pool's `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the original user. If the pool admin adds the router to the allowlist to support router-mediated swaps for allowed users, any unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap()` call. [1](#0-0) 

When `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`) is called, the router calls `pool.swap()` directly: [2](#0-1) 

The pool's `swap()` function passes `msg.sender` (the router address) as `sender` to `_beforeSwap`: [3](#0-2) 

`ExtensionCalling._beforeSwap` then forwards this router address as `sender` to the extension: [4](#0-3) 

The extension therefore checks whether the **router** is on the allowlist, not whether the **original user** is. This creates an irreconcilable dilemma for pool admins:

- **Router NOT on allowlist:** Allowed users cannot use the router at all — their router swaps are blocked because `sender = router` is not allowed.
- **Router IS on allowlist:** Any user (including non-allowlisted ones) can bypass the allowlist by routing through the router, because the extension sees `sender = router` (allowed) regardless of who initiated the call.

The same bypass applies to all router entry points: `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

Any user can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter`. This breaks the core invariant of `SwapAllowlistExtension`: that only explicitly allowed addresses can swap on a curated pool. Pools designed for specific user sets (e.g., KYC-gated, institutional-only) are accessible to arbitrary public users the moment the router is added to the allowlist. The bypass is direct and requires no privileged access — only a standard router call.

---

### Likelihood Explanation

The bypass is triggered when:
1. A pool has `SwapAllowlistExtension` configured in its `beforeSwap` extension order.
2. The pool admin has added the router to the allowlist.

Both conditions are realistic in production. The router is the primary user-facing entry point for swaps. Pool admins who want their allowed users to use the router must add it to the allowlist, inadvertently opening the pool to all users. There is no in-protocol mechanism to prevent this outcome — the extension architecture provides no way to forward the original caller's identity through the router.

---

### Recommendation

The `SwapAllowlistExtension` must check the original user's identity rather than the intermediary's. Options:

1. **Extension-data identity forwarding:** Require the router to encode the original caller's address in `extensionData`, and have the extension verify it (e.g., with a signature or trusted-forwarder pattern).
2. **Recipient-based gating:** Gate on `recipient` instead of `sender` if the pool's design intent is to restrict who receives swap output.
3. **Router-aware extension:** The extension could maintain a registry of trusted routers and, when `sender` is a trusted router, extract the real user from `extensionData`.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` registered in `beforeSwap` extension order.
2. Pool admin calls `setAllowedToSwap(pool, allowedUser, true)` and `setAllowedToSwap(pool, router, true)` to allow a specific user and the router.
3. `blockedUser` (not on allowlist) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: blockedUser, ...})`.
4. The router calls `pool.swap(blockedUser, zeroForOne, amount, priceLimit, "", extensionData)`.
5. The pool passes `msg.sender = router` as `sender` to `_beforeSwap` → `ExtensionCalling` → `SwapAllowlistExtension.beforeSwap`.
6. The extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. `blockedUser` successfully executes a swap on a pool they should be blocked from. [1](#0-0) [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
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
