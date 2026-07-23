Audit Report

## Title
`SwapAllowlistExtension` identifies the router as the swapper, allowing any user to bypass per-pool swap allowlists via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument forwarded from the pool, which is the pool's own `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, that caller is the router contract, not the end user. A pool admin who allowlists the router to permit allowlisted users to trade through the standard periphery inadvertently grants unrestricted swap access to every address, because the extension unconditionally approves any call arriving through the router.

## Finding Description
`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` encodes that value and dispatches it to every extension configured on `BEFORE_SWAP_ORDER`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` stores the real end user only in transient callback context (`_setNextCallbackContext`) for payment settlement, then calls `pool.swap()` directly — making the router the `msg.sender` inside the pool: [4](#0-3) 

The end user's address is never forwarded to the pool or to any extension. The extension has no mechanism to distinguish which end user initiated the router call. When the router is allowlisted, `allowedSwapper[pool][router] == true` passes for every call arriving through the router, regardless of who the actual caller is.

## Impact Explanation
`SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may trade against a pool. Bypassing it allows unauthorized users to execute swaps that the pool admin explicitly intended to block. For pools used as institutional-only liquidity, KYC-gated market making, or pools with favorable oracle pricing reserved for specific counterparties, this allows arbitrary users to drain LP value through unrestricted swaps. This satisfies "broken core pool functionality causing loss of funds" and "admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path."

## Likelihood Explanation
The trigger requires only two ordinary, expected operational actions by the pool admin:
1. Deploy a pool with `SwapAllowlistExtension` on `BEFORE_SWAP_ORDER`.
2. Call `setAllowedToSwap(pool, router, true)` — the natural step to let allowlisted users trade through the standard periphery.

After step 2, any address can call `MetricOmmSimpleRouter.exactInputSingle()` and bypass the allowlist. No special privilege, flash loan, or oracle manipulation is required. The admin action is not malicious; it is the expected operational step, and the bug is that it has an undocumented, fund-impacting side effect.

## Recommendation
The extension must verify the actual end user, not the intermediary. Two sound approaches:

1. **Pass the real swapper in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. Pool admins allowlist individual users, not the router.
2. **Dedicated router allowlist**: Maintain a separate `allowedRouter` set. When `sender` is an allowlisted router, decode the real swapper from `extensionData` and check that address against `allowedSwapper`.

The router address must never be the identity that the allowlist gates on.

## Proof of Concept
```
Setup
─────
1. Deploy pool with SwapAllowlistExtension on BEFORE_SWAP_ORDER.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // to let alice use the router

Attack
──────
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: bob})
   Inside pool.swap():
       msg.sender = router
       _beforeSwap(router, ...)
   Inside SwapAllowlistExtension.beforeSwap(sender=router, ...):
       allowedSwapper[pool][router] == true  →  check passes
5. Bob's swap executes and settles against LP funds.

Result: Bob, who was never allowlisted, successfully swaps against the pool,
        bypassing SwapAllowlistExtension entirely.
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
