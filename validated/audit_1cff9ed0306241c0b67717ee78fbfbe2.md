### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool sets `sender = msg.sender = router_address`. The extension therefore checks whether the **router** is allowlisted, not the actual user. To let allowlisted users trade through the router, the admin must allowlist the router address — but doing so opens the pool to **every** user, completely defeating the allowlist.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value just described: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router contract**, so `sender` delivered to the extension is the router address, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router_address]`.

This creates an inescapable dilemma for the pool admin:

1. **Router not allowlisted** — allowlisted users cannot trade through the router (broken core functionality).
2. **Router allowlisted** (the only fix) — every user on the network can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` through the public router and bypass the allowlist entirely.

The same identity mismatch applies to multi-hop `exactInput` for intermediate hops, where the router passes `address(this)` as the payer: [5](#0-4) 

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified market makers, whitelisted protocols) loses that restriction the moment the router address is added to the allowlist. Any unprivileged user can call the public router and execute swaps against the pool, draining LP assets at oracle-derived prices without being on the allowlist. The allowlist guard — the only access-control layer on the swap path — is rendered inoperative.

### Likelihood Explanation

The router is a public, permissionless contract. Any user who observes that allowlisted addresses trade through the router (and therefore that the router is allowlisted) can immediately exploit the bypass with a single `exactInputSingle` call. No special privileges, flash loans, or oracle manipulation are required. The trigger is a normal, valid transaction.

### Recommendation

Pass the **original caller** through the swap path rather than the immediate `msg.sender`. One approach: have the router encode the originating user in `callbackData` or `extensionData` and have the extension decode it. A cleaner protocol-level fix is to add an `originator` field to the swap call that the pool passes to extensions alongside `sender`, so extensions can gate on the economic actor rather than the intermediary. At minimum, document that allowlisting the router address opens the pool to all users, and provide a router-aware extension that reads the payer from transient callback context.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  admin calls setAllowedToSwap(pool, router, true)  // required so alice can use the router

Attack:
  charlie (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: charlie,
      zeroForOne: true,
      amountIn: X,
      ...
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=charlie, ...)   // msg.sender at pool = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
        → swap executes, charlie receives tokens

Result: charlie, who is not on the allowlist, successfully swaps against the pool.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```
