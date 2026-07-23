Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router's address rather than the actual end user. If the pool admin allowlists the router — a necessary step for any allowlisted user to use the router — every unprivileged user can bypass the allowlist by calling the public router, receiving pool tokens at oracle-derived prices without authorization.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the caller of the extension) and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router `msg.sender` inside the pool: [3](#0-2) 

The actual end user (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment purposes and is never forwarded to the pool or extension as the swap initiator. The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`.

This creates an inescapable dilemma: to allow any allowlisted user to use the router, the admin must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every caller of the router, including non-allowlisted users. There is no additional guard in the router or extension that checks the actual `msg.sender` of the router call.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` entry points, all of which call `pool.swap()` with the router as `msg.sender`. [4](#0-3) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified users, whitelist-only participants) can be fully bypassed by any unprivileged user routing through the public `MetricOmmSimpleRouter`. The unauthorized user receives pool tokens at oracle-derived prices, draining pool reserves in a manner the pool admin explicitly intended to prevent. This breaks the core access-control invariant of the allowlist extension and constitutes a direct loss of LP assets to unauthorized counterparties — a broken core pool functionality causing loss of funds above Sherlock thresholds.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract deployable and callable by anyone.
- The bypass requires only that the router is allowlisted, which is a necessary operational step for any allowlisted user to use the router at all.
- No privileged access, flash loans, or special setup is required — a single `exactInputSingle` call suffices.
- Pool admins deploying `SwapAllowlistExtension` for compliance or access-control purposes are the primary target, and the bypass is invisible to them once the router is allowlisted.

## Recommendation

The extension must gate the **actual end user**, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires coordinated changes to the router and extension, and the extension must trust that the encoding is authentic (e.g., by verifying the caller is a known router).

2. **Gate at the router level**: Add an allowlist check inside the router before calling `pool.swap()`, and document that the pool's allowlist must include the router address only when the router enforces its own user-level check. The pool-level extension then serves as a secondary guard for direct pool callers.

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
  2. Router calls pool.swap(bob, true, X, ...) — msg.sender inside pool = router address
  3. Pool calls extension.beforeSwap(router, bob, ...)
  4. Extension checks: allowedSwapper[pool][router] == true  ✓
  5. Swap executes — bob receives tokens from the pool
  6. Allowlist is bypassed; bob trades in a pool he was never authorized to access
```

Relevant code confirming the check uses `sender` (the router) rather than the actual user: [5](#0-4)

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
