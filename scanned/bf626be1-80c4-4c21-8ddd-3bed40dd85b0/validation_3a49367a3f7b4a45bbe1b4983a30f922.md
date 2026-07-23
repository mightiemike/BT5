### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the pool call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router address**, not the original user. If the pool admin allowlists the router to support router-mediated swaps, every unprivileged user can bypass the per-user allowlist by routing through the public router contract.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the pool's `msg.sender` the router, not the original user: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Consequence**: A pool admin who wants to support router-based swaps for their allowlisted users must add the router address to the allowlist. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, so the extension passes for **every** user who routes through the router, regardless of whether that user is individually allowlisted. The per-user gate is completely bypassed.

The invariant the extension is supposed to enforce — "only addresses in the allowlist may swap" — is broken for any pool that also supports router-mediated swaps.

### Impact Explanation

Any user who is not individually allowlisted can swap in a restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The pool receives and settles the swap normally; the only guard that was supposed to block the user silently passes because it sees the router's address. This is a direct loss-of-access-control impact: tokens flow through a pool that was designed to be restricted, potentially allowing sanctioned, non-KYC'd, or otherwise excluded addresses to trade.

### Likelihood Explanation

The trigger is fully unprivileged — any EOA or contract can call the public router. The precondition (router is allowlisted) is a natural operational step: any pool admin who wants their allowlisted users to be able to use the router must allowlist it. The bypass is therefore reachable in any production deployment that combines `SwapAllowlistExtension` with `MetricOmmSimpleRouter` support.

### Recommendation

The extension must gate the **original user**, not the intermediary. Two sound approaches:

1. **Pass the original caller through the router**: Add an `originalSender` field to the router's `extensionData` payload and have the extension decode and check it. The router already controls `extensionData` forwarding.

2. **Check `sender` against a router-aware allowlist**: Extend `SwapAllowlistExtension` with a separate `allowedRouter` mapping; when `sender` is a known router, additionally verify that the router's `extensionData` carries a user address that is itself allowlisted.

Either way, the extension must not treat the router address as the identity to gate.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension (beforeSwap order)
  allowedSwapper[pool][alice]  = true   // alice is the intended user
  allowedSwapper[pool][router] = true   // admin adds router to support alice's router swaps

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ..., recipient: bob})

  Call chain:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (passes)
        → swap executes, bob receives output tokens

Result:
  bob swaps successfully in a pool he was never allowlisted for.
  The allowlist invariant is violated; the pool's access control is broken.
``` [6](#0-5) [7](#0-6)

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
