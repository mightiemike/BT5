Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the original user, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. If the pool admin allowlists the router to enable router-mediated swaps for allowlisted users, every user—including those explicitly excluded—can bypass the per-user gate by calling any `exact*` function on the router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every registered extension, including `SwapAllowlistExtension`.

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of the pool. The original caller's address is stored only in transient storage via `_setNextCallbackContext` for payment settlement and is never forwarded to the pool or the extension: [3](#0-2) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` with the router as `msg.sender`. [4](#0-3) 

The pool admin faces an impossible choice: do not allowlist the router (allowlisted users cannot use the router at all), or allowlist the router (every user bypasses the gate).

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swapping to a curated set of addresses loses that protection entirely for any user routing through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against the pool's liquidity at oracle-derived prices. LP funds are directly at risk because the allowlist was the only mechanism preventing those users from trading against the pool. This constitutes a broken core pool functionality causing direct loss of LP assets and unauthorized access to pool liquidity.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address, which simultaneously opens the bypass to all users. The router is a deployed, public, permissionless contract requiring no special access or setup. The attack requires only a standard `exactInputSingle` call from any EOA.

## Recommendation

The pool's `swap()` function should accept an explicit `swapper` parameter (the economically relevant actor) separate from `msg.sender` (the settlement payer), and pass that value as `sender` to extensions. Alternatively, `SwapAllowlistExtension.beforeSwap` should decode the original caller's address from `extensionData` when the caller is a known router, and the router should populate `extensionData` with `msg.sender` before calling the pool. The simplest safe fix is to add a `swapper` field to the pool's `swap` signature and have the router populate it with `msg.sender` before calling the pool.

## Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Pool admin allowlists the router so that allowlisted users can trade via it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Alice is NOT on the per-user allowlist.
// Direct swap reverts:
vm.prank(alice);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(alice, true, 1000, 0, "", "");

// But Alice bypasses the allowlist via the router:
vm.prank(alice);
// Succeeds — extension sees sender == address(router), which IS allowlisted.
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: alice,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Alice receives token1 output — allowlist bypassed.
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
