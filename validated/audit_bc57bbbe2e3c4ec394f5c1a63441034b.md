Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `MetricOmmPool.swap()`. When any user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address. If the pool admin allowlists the router (a necessary operational step for any allowlisted user to use the router), every non-allowlisted user can bypass the swap allowlist by calling the public router, receiving pool tokens they were never authorized to access.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly with no forwarding of the original caller: [3](#0-2) 

The extension therefore sees `sender = router address`, not the actual end user. The allowlist check becomes `allowedSwapper[pool][router]`. This creates an inescapable dilemma: to allow any allowlisted user to use the router, the admin must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every caller regardless of their identity, because the extension has no visibility into who initiated the router call. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` entry points in the router. [4](#0-3) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified users, whitelist-only participants) is fully bypassed by any unprivileged user routing through the public `MetricOmmSimpleRouter`. The unauthorized user receives pool tokens at oracle-derived prices, draining pool reserves in a way the pool admin explicitly intended to prevent. This constitutes a direct admin-boundary break — an unprivileged path circumvents the pool admin's access-control configuration — and results in direct loss of LP assets to unauthorized counterparties, meeting the contest's Critical/High impact threshold.

## Likelihood Explanation
- `MetricOmmSimpleRouter` is a public, permissionless contract — any user can call it without any special access.
- The bypass requires only that the router is allowlisted, which is a necessary operational step for any allowlisted user to use the router at all.
- No privileged access, no special tokens, no malicious setup is required. A single `exactInputSingle` call suffices.
- Pool admins deploying `SwapAllowlistExtension` for compliance or access-control purposes are the primary target, and the bypass is invisible to them once the router is allowlisted.

## Recommendation
The `SwapAllowlistExtension` must gate the actual end user, not the direct caller of `pool.swap()`. The preferred fix is to have the router encode the actual `msg.sender` into `extensionData`, and have the extension decode and verify it when the caller is a known trusted router. Alternatively, the extension could be redesigned to check `recipient` for single-hop swaps, though this breaks for multi-hop paths. A third option is to add an allowlist check inside the router before calling `pool.swap()`, and document that the pool's allowlist must include the router address only when the router enforces its own user-level check. [5](#0-4) 

## Proof of Concept
```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only intended swapper
  - Pool admin calls setAllowedToSwap(pool, router, true)  // required so alice can use the router

Attack:
  1. bob (not allowlisted) calls:
       router.exactInputSingle({
         pool: pool,
         recipient: bob,
         zeroForOne: true,
         amountIn: X,
         ...
       })
  2. Router calls pool.swap(bob, true, X, ...) — msg.sender to pool = router address
  3. Pool calls _beforeSwap(router, bob, ...)
  4. Extension checks: allowedSwapper[pool][router] == true  ✓
  5. Swap executes — bob receives tokens from the pool
  6. Allowlist is bypassed; bob trades in a pool he was never authorized to access
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
