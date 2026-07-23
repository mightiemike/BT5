Audit Report

## Title
`SwapAllowlistExtension` Gates on Router Address Instead of Actual Swapper, Enabling Complete Allowlist Bypass - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` at the pool level — the router contract address, not the originating user. When a pool admin allowlists the router (required for any router-mediated swap to succeed), every user on the network can bypass the allowlist by routing through `MetricOmmSimpleRouter`, because the extension sees `sender = router` and approves the call regardless of who initiated it.

## Finding Description
The call chain is:

1. `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [1](#0-0) 

2. `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension: [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router — not the originating user: [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no mechanism to forward the original `msg.sender` into the extension check: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The pool admin faces an impossible choice: allowlist the router (enabling all users to bypass the allowlist) or don't allowlist it (making the router unusable for allowlisted users). There is no existing guard that resolves the original caller identity at the extension level.

## Impact Explanation
The `SwapAllowlistExtension` is the on-chain mechanism for restricting swaps to vetted counterparties. When bypassed, any unpermissioned user can execute swaps against the pool's liquidity at oracle-derived prices, exposing LP principal to the full universe of traders rather than the curated set the admin intended. The pool admin has no on-chain recourse because the extension has no per-user granularity at the router level. This is a direct loss of curation policy and LP principal, matching the "allowlist bypass" impact class.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical periphery entry point for swaps. The bypass requires no special knowledge, no privileged access, and no unusual token behavior — only calling the public router with a valid swap path. The only precondition is that the pool admin has allowlisted the router, which is the only way to make router-mediated swaps work at all on an allowlisted pool, making this a near-certain operational condition.

## Recommendation
The extension must resolve the original caller rather than the pool-forwarded `sender`. The router should encode `msg.sender` into `extensionData` before calling the pool (transient storage is already used for the payer context). `SwapAllowlistExtension.beforeSwap` would then decode and check the original caller from `extensionData` when `sender` is a recognized router address, rather than checking `sender` directly.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, allowedUser, true)
  - Pool admin calls setAllowedToSwap(pool, routerAddress, true)
    (required so allowedUser can use the router)

Attack:
  - attacker (not in allowlist) calls:
      router.exactInputSingle({pool: pool, tokenIn: token0, tokenOut: token1,
                                recipient: attacker, amountIn: X, ...})
  - router calls pool.swap(attacker, ...) with msg.sender = router
  - pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes successfully for the non-allowlisted attacker

Result:
  - attacker swaps against LP liquidity on a pool they were never permitted to access
  - allowlist is completely bypassed via the public router
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
