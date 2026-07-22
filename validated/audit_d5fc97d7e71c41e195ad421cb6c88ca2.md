### Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the **router contract address**, not the originating user. A pool admin who allowlists the router to enable router-mediated swaps for their allowlisted users inadvertently opens the gate to every user, completely bypassing the allowlist.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows: [1](#0-0) 

The `sender` argument is the first parameter forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` of the pool's `swap` call: [2](#0-1) [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the **router**, not the original user: [4](#0-3) [5](#0-4) 

The extension therefore evaluates `allowedSwapper[pool][router]` for every router-mediated swap, regardless of who called the router.

### Impact Explanation

A pool admin who wants to allow their allowlisted users to swap via the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **any** address — including addresses the admin explicitly never allowlisted — can call `router.exactInputSingle(...)` and the extension check passes because it sees `sender = router`. The swap allowlist is completely neutralised for all router-mediated swaps. Non-allowlisted users can freely swap against the pool, draining LP principal through oracle-priced trades.

### Likelihood Explanation

The operator pattern is explicitly documented: `msg.sender` pays but need not equal `owner`. [6](#0-5) 

A pool admin who deploys a swap-allowlisted pool and also wants users to access it through the public router has no other option than to allowlist the router. The mistake is structurally forced by the design: the extension provides no way to gate the originating user when an intermediary is involved. The `MetricOmmSimpleRouter` is a public, permissionless contract, so once the router is allowlisted the bypass is available to anyone.

### Recommendation

Pass and check the **originating user** rather than the immediate caller. Two concrete approaches:

1. **Extension-side fix**: Change `beforeSwap` to check `sender` only when `msg.sender` (the pool's caller) is not a known periphery contract, or require the router to forward the original user in `extensionData` and verify it there.
2. **Router-side fix**: Have the router encode `msg.sender` (the original user) into `extensionData` and have the extension decode and gate on that value instead of the raw `sender` argument.
3. **Simplest correct fix**: Gate on `sender` only when `sender` is not a registered periphery contract; for registered periphery contracts, require the original user to be encoded in `extensionData` and verified by the extension.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as extension1, beforeSwap order = extension1.
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended gated user
  - Pool admin calls setAllowedToSwap(pool, router, true)      // to let alice use the router

Attack:
  - charlie (never allowlisted) calls:
      router.exactInputSingle({pool: pool, ..., recipient: charlie})
  - Router calls pool.swap(...) with msg.sender = router
  - Extension evaluates: allowedSwapper[pool][router] == true  → passes
  - Charlie's swap executes against the pool, bypassing the allowlist entirely.

Verification:
  - charlie calling pool.swap(...) directly reverts with NotAllowedToSwap (allowedSwapper[pool][charlie] == false).
  - charlie calling router.exactInputSingle(...) succeeds because the extension sees sender = router.
``` [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L147-150)
```text
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
  /// @param salt Namespace byte width for the key (`uint80`).
  /// @param deltas Parallel `binIdxs` / `shares` arrays (see `LiquidityDelta`).
```
