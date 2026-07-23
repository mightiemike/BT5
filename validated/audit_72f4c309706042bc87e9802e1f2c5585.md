Audit Report

## Title
Swap Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the pool's immediate `msg.sender` (the `sender` parameter), not the originating user. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract. If the router is allowlisted on a curated pool — which is required for any allowlisted user to use the router — every unprivileged user can bypass the swap allowlist by routing through the router, entirely defeating the access control policy.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap` — i.e., the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The result is a structural dilemma: if the router is not allowlisted, no user can use the router on the pool (including allowlisted ones); if the router is allowlisted, every user — including those not on the allowlist — can swap freely by calling any `exact*` function on the router. There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users.

The deposit allowlist is not affected: `DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the position beneficiary), not the caller's address, so the liquidity adder does not introduce an analogous bypass: [6](#0-5) 

## Impact Explanation
Any user not on the swap allowlist can trade on a curated pool by calling any `exact*` function on `MetricOmmSimpleRouter`, provided the router is allowlisted. The pool admin's access control policy is entirely defeated for the router path. This constitutes an admin-boundary break where an unprivileged actor bypasses a pool-level restriction that the admin explicitly configured, enabling unauthorized trading on pools intended to be restricted.

## Likelihood Explanation
The router is the primary supported swap interface. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address, which immediately opens the bypass to all users. No special attacker capability is required beyond calling a public router function. The condition is met by any pool that uses `SwapAllowlistExtension` and also permits router-mediated swaps.

## Recommendation
Pass the originating user's address through the router to the pool, either as part of `extensionData` or a dedicated field, and have the pool forward it as a separate `originator` argument to extension hooks. Alternatively, add an `originator` field to the `beforeSwap` hook signature that the pool populates from a verified source rather than from `msg.sender`. The simplest near-term fix is for the router to embed `msg.sender` in `extensionData` and for `SwapAllowlistExtension` to decode and check that value when present, though this requires a protocol-level convention.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Allowlist only `alice` as a swapper on the pool (`allowedSwapper[pool][alice] = true`).
3. Also allowlist the router address so `alice` can use the router (`allowedSwapper[pool][router] = true`).
4. Call `MetricOmmSimpleRouter.exactInputSingle` as `bob` (not on the allowlist).
5. The pool calls `_beforeSwap(router, ...)`, the extension checks `allowedSwapper[pool][router]` — the router is allowlisted — and the call succeeds.
6. `bob` has traded on a pool that was supposed to block him.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
