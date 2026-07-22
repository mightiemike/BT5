### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. If the pool admin allowlists the router (which is required to permit any router-mediated swaps), every user—including those explicitly not allowlisted—can bypass the curation gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the pool's `msg.sender` the router, not the end user: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Result**: the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. A pool admin who wants to permit router-mediated swaps for allowlisted users must add the router to the allowlist. Once the router is allowlisted, every user—regardless of their individual allowlist status—can swap through the router and pass the guard.

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` is designed to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, institutional LPs, or whitelisted market makers). The bypass allows any unprivileged user to trade against the pool's liquidity. Because the pool prices are oracle-anchored, an unauthorized trader can extract value from LP positions at the oracle mid-price minus spread, directly reducing LP principal. This matches the **allowlist bypass** impact class: disallowed users can trade on a curated pool, causing direct loss of LP assets.

---

### Likelihood Explanation

The trigger requires only that the pool admin has allowlisted the router—a natural operational step for any pool that intends to support router-mediated swaps. No privileged action by the attacker is needed; calling `MetricOmmSimpleRouter.exactInputSingle` is a standard public entrypoint. Any user who knows the pool is allowlist-gated can attempt the bypass immediately.

---

### Recommendation

Pass the original end-user address through the swap path so the extension can gate on the economically relevant actor. One approach: add an `originator` field to the swap call or to `extensionData` that the router populates with `msg.sender` before calling the pool, and update `SwapAllowlistExtension.beforeSwap` to read and verify that field. Alternatively, document that the allowlist gates the direct pool caller only and that router-mediated swaps are ungated, so pool admins do not configure the extension with the expectation of per-user router gating.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. Attacker calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Pool calls `_beforeSwap(router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. Attacker's swap executes against LP liquidity despite never being individually allowlisted.

The extension's `isAllowedToSwap(pool, attacker)` returns `false` throughout, confirming the bypass is invisible to the allowlist state. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L27-41)
```text
  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
