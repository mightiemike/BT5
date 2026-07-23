Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the originating user, allowing any unprivileged user to bypass the swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of `pool.swap()`, so the extension checks whether the router is allowlisted rather than the originating user. Any pool admin who allowlists the router (required for any allowlisted user to use it) simultaneously grants every unprivileged user the ability to bypass the curated swap allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool (`msg.sender` inside the extension = the pool): [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly, making the router the `msg.sender` at the pool call site: [4](#0-3) 

The pool therefore passes `sender = router` to the extension. The extension checks `allowedSwapper[pool][router]`. The originating user's address is never consulted. The same issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` with the router as `msg.sender`. [5](#0-4) 

No existing guard compensates for this: `extensionData` is passed through from the caller unchanged and the extension does not decode any originating-user field from it. [6](#0-5) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers). Any unprivileged user can bypass this restriction entirely by calling `MetricOmmSimpleRouter.exactInputSingle` (or the multi-hop variants). The bypass requires no special privilege, no admin action, and no non-standard token behavior — only the publicly deployed router. The result is direct unauthorized access to a pool whose core invariant is restricted swap access, which can cause loss of LP principal if the pool's liquidity was sized or priced for a controlled counterparty set. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard, publicly deployed periphery swap entry point. Any user who discovers the allowlist restriction on a direct `pool.swap()` call will naturally try the router as an alternative path. No privileged access, no special setup, and no malicious initial configuration is required. The router must be allowlisted for any allowlisted user to use it, making the vulnerable configuration the only operationally viable one.

## Recommendation
The `SwapAllowlistExtension` must gate the originating user, not the immediate caller of `pool.swap()`. The cleanest fix is to have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` before forwarding to the pool, and have `SwapAllowlistExtension.beforeSwap` decode and verify it against the allowlist. This requires a coordinated convention between the router and the extension but ensures the allowlist always gates the originating EOA regardless of routing path. Alternatively, document that pools using `SwapAllowlistExtension` must not allowlist the router and that allowlisted users must call `pool.swap()` directly — but this is operationally fragile and breaks the standard periphery flow.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in beforeSwap order
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router must be allowed for alice to use it
  - bob is NOT on the allowlist

Attack:
  1. bob calls pool.swap(...) directly
     → SwapAllowlistExtension checks allowedSwapper[pool][bob] → false → reverts NotAllowedToSwap ✓

  2. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
     → router calls pool.swap() with msg.sender = router
     → pool calls _beforeSwap(sender=router, ...)
     → SwapAllowlistExtension checks allowedSwapper[pool][router] → true → PASSES ✗
     → bob's swap executes on the curated pool without authorization

Foundry test outline:
  - Deploy SwapAllowlistExtension, configure pool with it in BEFORE_SWAP_ORDER
  - setAllowedToSwap(pool, router, true); setAllowedToSwap(pool, alice, true)
  - vm.prank(bob); router.exactInputSingle(...) → assert no revert
  - vm.prank(bob); pool.swap(...) → assert revert NotAllowedToSwap
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
