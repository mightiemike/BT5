Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of End-User, Allowing Any Unprivileged Caller to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is the pool's immediate `msg.sender` — the router contract — not the originating user. When the pool admin allowlists `MetricOmmSimpleRouter` (required for any router-mediated swap to succeed), every address on the network can bypass the allowlist by routing through the public router. The extension's access control collapses from per-user gating to a single binary check on whether the router is allowlisted.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` of the pool call: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

The result is a structural contradiction: the pool admin cannot simultaneously (a) allow router-mediated swaps by adding the router to `allowedSwapper` and (b) restrict which end-users may swap, because every user can call the public router. No existing guard in `SwapAllowlistExtension` inspects `extensionData` for an originating user address, and the pool passes no additional caller context beyond `msg.sender`.

## Impact Explanation
Any user explicitly excluded from the allowlist can bypass the restriction by calling `MetricOmmSimpleRouter.exactInputSingle` (or any multi-hop variant). The `NotAllowedToSwap` revert is never reached because the check passes on the router's address. Pools relying on `SwapAllowlistExtension` for access control — permissioned liquidity pools, compliance-gated venues, or pools restricted to specific market makers — are fully open to arbitrary swappers the moment the router is allowlisted. This constitutes a broken core pool functionality and an admin-boundary break: the pool admin's explicit exclusion of an address is bypassed by an unprivileged path through the public router.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who enables the allowlist extension and also wants users to swap via the router must allowlist the router — at which point the bypass is immediately available to every address on the network. No special privileges, flash loans, or multi-step setup are required; a single `exactInputSingle` call suffices. The precondition (router allowlisted) is a normal operational requirement, not an edge case.

## Recommendation
The extension must gate on the end-user identity, not the immediate pool caller. Two complementary approaches:

1. **Pass originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires a coordinated convention between router and extension.

2. **Dedicated `allowedRouter` mapping with user forwarding**: Add a separate `allowedRouter` mapping so the extension can distinguish "this is a trusted router" from "this specific user is allowed." The router supplies the real user address in `extensionData` for the extension to verify.

The core invariant that must hold: the identity checked against the allowlist must be the economic actor who benefits from the swap output, not the contract that mechanically forwards the call.

## Proof of Concept
```
Setup:
  pool deployed with SwapAllowlistExtension, beforeSwap order = extension 1
  pool admin calls setAllowedToSwap(pool, router, true)   // router allowlisted (required for router swaps)
  pool admin calls setAllowedToSwap(pool, alice, true)    // alice allowlisted
  // bob is NOT allowlisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
  })

  → router calls pool.swap(bob, true, X, ...) with msg.sender = router
  → pool calls _beforeSwap(sender=router, ...)            // MetricOmmPool.sol L230-240
  → SwapAllowlistExtension.beforeSwap receives sender=router
  → checks allowedSwapper[pool][router] == true  ✓        // SwapAllowlistExtension.sol L37
  → swap proceeds; bob receives output tokens

  Direct call (for comparison):
  bob calls pool.swap(...) directly
  → _beforeSwap(sender=bob, ...)
  → checks allowedSwapper[pool][bob] == false → NotAllowedToSwap ✗

Result: bob bypasses the allowlist via the router with a single transaction.
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
