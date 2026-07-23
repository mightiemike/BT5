Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of original user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which resolves to `msg.sender` of the pool's `swap` call — the router contract, not the originating EOA. When a pool admin allowlists the router to permit allowlisted users to trade through it, the check `allowedSwapper[pool][router]` passes for every caller of the router, regardless of whether that caller is on the allowlist. Any unprivileged user can bypass the curated pool's access control by routing through `MetricOmmSimpleRouter`.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

Therefore the check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`. A pool admin who wants allowlisted users to trade via the router must add the router to the allowlist. Once the router is allowlisted, the check passes for **every** caller of the router. This is confirmed by contrast with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly gates on `owner` (the position owner, explicitly passed through the call stack) rather than `sender`: [5](#0-4) 

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in the router. [6](#0-5) 

## Impact Explanation
A curated pool that restricts swaps to specific market makers or whitelisted counterparties loses its access control entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the pool and the `beforeSwap` hook will pass. This enables unauthorized users to execute trades the pool designer explicitly intended to block — constituting broken core pool access-control functionality and potential direct loss of LP principal or protocol fees.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who deploys `SwapAllowlistExtension` and wants allowlisted users to trade via the router has no alternative but to add the router to the allowlist; the extension provides no mechanism to thread the original EOA identity through the router. The admin's natural, well-intentioned action (allowlist the router) is exactly the action that opens the bypass. No malicious setup is required; the attacker only needs to call the public router.

## Recommendation
Replace the `sender` check in `SwapAllowlistExtension.beforeSwap` with a check on the economically relevant actor:

1. **Decode original user from `extensionData`**: Require the router to ABI-encode the original `msg.sender` into `extensionData` and have the extension decode and verify it. This mirrors how `DepositAllowlistExtension` uses the explicitly passed `owner`.
2. **Gate on `recipient`**: If the pool's usage convention guarantees `recipient == original user`, gate on `recipient` instead of `sender`. This is weaker but avoids router changes.

The cleanest fix is option 1: the router encodes `abi.encode(msg.sender)` into `extensionData` for every swap hop, and the extension decodes and checks that address against `allowedSwapper[pool][originalUser]`.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)    // alice is allowlisted
  admin calls setAllowedToSwap(pool, router, true)   // router allowlisted so alice can use it

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
  })

  router calls pool.swap(bob, true, X, ...)
    msg.sender to pool = router
    pool calls _beforeSwap(router, bob, ...)
    SwapAllowlistExtension.beforeSwap(sender=router, ...)
      allowedSwapper[pool][router] == true  ✓  (passes)

  bob's swap executes on the curated pool despite not being allowlisted.
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
