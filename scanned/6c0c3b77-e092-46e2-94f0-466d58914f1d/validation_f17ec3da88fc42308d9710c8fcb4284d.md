### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router to support router-mediated swaps, every user — including non-allowlisted ones — can bypass the restriction by going through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The result is that the extension always sees the router's address as the swapper identity when the router is used, never the actual end-user.

### Impact Explanation

Two fund-impacting outcomes follow from this wrong-actor binding:

**Bypass (High):** A pool admin who wants to allow router-mediated swaps for allowlisted users must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, every user — including those explicitly excluded — can call `MetricOmmSimpleRouter.exactInputSingle` and pass the allowlist check, because the extension sees the router address and finds it allowlisted. The entire curation boundary collapses: non-KYC'd or otherwise restricted users trade freely on a pool designed to exclude them, directly accessing LP liquidity they should not reach.

**Broken functionality (Medium):** If the pool admin does not allowlist the router, allowlisted users who call through the router have their swaps reverted (`NotAllowedToSwap`), even though they are individually permitted. The router — the protocol's own supported periphery path — is unusable on any allowlisted pool.

### Likelihood Explanation

The trigger is a standard user action: calling `MetricOmmSimpleRouter.exactInputSingle` on a pool that has `SwapAllowlistExtension` configured. No special privilege, flash loan, or exotic token is required. Any user who knows the pool is allowlist-gated can attempt the bypass. The pool admin enabling the router (a natural operational step) is the only precondition for the full bypass.

### Recommendation

The extension must check the economically relevant actor — the end-user — not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router.** The router already stores the original `msg.sender` in transient storage as the payer. Thread it through as a separate `initiator` field in `extensionData` or as an additional pool-level argument, and have the extension read that value instead of `sender`.

2. **Gate on `sender` only for direct pool calls; reject router-mediated calls on allowlisted pools.** Alternatively, document that `SwapAllowlistExtension` is incompatible with router use and enforce this at the factory level by rejecting pools that configure both.

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is KYC'd
  allowedSwapper[pool][bob]   = false         // bob is excluded
  allowedSwapper[pool][router] = true         // admin enables router for alice

Attack (bob):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — pool's msg.sender = router
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. Extension evaluates: allowedSwapper[pool][router] == true  → passes
  5. Swap executes; bob receives output tokens from the restricted pool

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
```

The allowlist invariant — "only explicitly permitted addresses may swap on this pool" — is broken for every user whenever the router is allowlisted, which is the only way to make the router usable on an allowlisted pool at all.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
