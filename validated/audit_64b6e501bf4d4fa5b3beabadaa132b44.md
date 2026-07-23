Audit Report

## Title
`SwapAllowlistExtension.beforeSwap()` checks router address instead of actual end user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `msg.sender` of `pool.swap()` is the router contract, not the end user. If the router is allowlisted, every user — including those explicitly excluded — can bypass the per-pool swap allowlist by routing through the router.

## Finding Description

**Root cause in `SwapAllowlistExtension.beforeSwap()`:**

The extension checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the first argument passed by the pool: [1](#0-0) 

**What the pool passes as `sender`:**

`MetricOmmPool.swap()` passes its own `msg.sender` (the immediate caller) as `sender` to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap()` then forwards this as the first argument to `IMetricOmmExtensions.beforeSwap`: [3](#0-2) 

**What `msg.sender` is when the router is used:**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` of `pool.swap()`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Result:** The extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. Once the router is allowlisted, the check passes unconditionally for every user of the router.

**Contrast with `DepositAllowlistExtension`:**

`DepositAllowlistExtension.beforeAddLiquidity()` correctly ignores `sender` and checks `owner` (the second argument), which is an explicit caller-supplied identity that survives router indirection: [6](#0-5) 

The swap interface has no equivalent "actual user" field; `sender` is the only identity the extension receives, and it collapses to the router address on every router-mediated call.

## Impact Explanation

Any user excluded from a curated pool's swap allowlist can trade on that pool by routing through `MetricOmmSimpleRouter`. The curation invariant — "only allowlisted addresses may swap" — is broken for every pool that allowlists the router. Disallowed users execute swaps that consume LP liquidity and generate fees on a pool configured to exclude them. Depending on the pool's purpose (KYC gating, institutional-only access, regulatory compliance), this constitutes a direct policy bypass with fund-impacting consequences.

## Likelihood Explanation

Allowlisting the router is a natural operational choice: a pool admin who wants to support the standard periphery UX while still restricting direct pool access will allowlist the router. The admin has no indication from the extension's interface or documentation that allowlisting the router implicitly grants access to all users. The `SwapAllowlistExtension` is documented as "Gates `swap` by swapper address, per pool," implying per-user granularity that does not survive router indirection.

## Recommendation

The `beforeSwap` hook should gate on the actual economic actor, not the immediate caller. Two options:

1. **Add an explicit `actualUser` field to the swap interface**: the pool passes a caller-verified identity alongside `sender`, and the extension checks that field.
2. **Check `recipient` with a documented convention**: require that the router always sets `recipient` to the actual end user and document that the allowlist checks `recipient`. This is weaker because `recipient` is caller-controlled, but it is the only other identity available in the current interface.

Until the interface is extended, pool admins must be warned that allowlisting the router in `SwapAllowlistExtension` grants unrestricted swap access to all users of that router.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, router, true)` to allow router-based swaps.
3. Pool admin does NOT allowlist `userB` (or explicitly sets `allowedSwapper[pool][userB] = false`).
4. `userB` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: userB, ...})`.
5. Router calls `pool.swap(userB, ...)` — `msg.sender` of `pool.swap()` = router.
6. Pool calls `extension.beforeSwap(router, userB, ...)` — `msg.sender` = pool, `sender` = router.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. `userB`'s swap executes successfully despite being excluded from the allowlist.

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
