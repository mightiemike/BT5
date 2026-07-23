### Title
`SwapAllowlistExtension` validates the router intermediary instead of the actual swapper, allowing any user to bypass the curated-pool allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps, every user on the network can bypass the allowlist by calling the router.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value verbatim as the first argument of the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making itself `msg.sender` of that call: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The result is that the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. The actual user's address is never visible to the guard.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a named set of addresses. To let those addresses use the standard router, the admin must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller of the router, including addresses the admin explicitly never allowlisted. The allowlist guard is completely neutralised for router-mediated swaps. Any user can drain the curated pool's liquidity at oracle prices, bypassing the curation policy entirely.

If the admin does not allowlist the router, the inverse problem occurs: allowlisted users cannot use the router at all, breaking the expected swap UX for the pool.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical periphery swap entry point documented and expected by users. Pool admins who deploy curated pools with `SwapAllowlistExtension` will routinely allowlist the router so their permitted users can swap normally. The bypass requires no special privilege, no flash loan, and no multi-step setup — any address calls `exactInputSingle` on the router pointing at the curated pool.

---

### Recommendation

The extension must gate the economically relevant actor, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** Add a `swapper` field to the router's `ExactInputSingleParams` (and equivalent structs) that defaults to `msg.sender`. Encode it into `extensionData` and have `SwapAllowlistExtension.beforeSwap()` decode and check it when present, falling back to `sender` for direct pool calls.

2. **Alternatively, check `recipient` instead of `sender`.** For direct swaps the recipient is often the user; however this is also spoofable, so option 1 is preferred.

The cleanest long-term fix is for the pool to expose a separate `originalCaller` field (analogous to ERC-1271's `isValidSignature` checking the actual signer rather than the forwarding contract), so every extension can gate the true economic actor regardless of the call path.

---

### Proof of Concept

```
Setup
─────
1. Deploy MetricOmmPool with SwapAllowlistExtension (beforeSwap order = 1).
2. Pool admin calls setAllowedToSwap(pool, ALICE, true).
   ALICE is the only permitted swapper.
3. Pool admin calls setAllowedToSwap(pool, address(router), true)
   so ALICE can use the router.

Attack
──────
4. BOB (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: BOB, ...})
5. Router calls pool.swap(BOB, ...) — msg.sender of pool.swap = router.
6. Pool calls extension.beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] → true → passes.
8. BOB's swap executes at oracle price; BOB receives token output.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist bypassed.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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
