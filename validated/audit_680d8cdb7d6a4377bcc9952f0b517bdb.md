Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates on Router Address Instead of Originating User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is `msg.sender` as seen by the pool at call time. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address, not the originating EOA. This makes the allowlist key non-unique across users: allowlisting the router grants unrestricted access to all users, and allowlisting individual EOAs blocks them from using the standard router entirely.

## Finding Description

In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the `sender` argument: [1](#0-0) 

In `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly on behalf of the user, making the pool's `msg.sender` the router address: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router address — not the originating user: [3](#0-2) 

The allowlist mapping is keyed `(pool => swapper => bool)`: [4](#0-3) 

Two exploitable configurations result:

**Config A — Router is allowlisted:** Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle()`. The pool sees `sender = router`. The check `allowedSwapper[pool][router] == true` passes. The swap executes for any user regardless of their individual allowlist status. The allowlist is completely bypassed.

**Config B — Individual users are allowlisted:** An allowlisted EOA calls `exactInputSingle()`. The pool sees `sender = router`. The router is not in the allowlist. The swap reverts with `NotAllowedToSwap`. Legitimate allowlisted users cannot use the standard periphery router at all.

The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in `MetricOmmSimpleRouter`. [5](#0-4) [6](#0-5) 

## Impact Explanation

Config A constitutes a broken access-control invariant with direct fund impact: any unprivileged user can execute swaps on a pool intended to be restricted, enabling unauthorized parties to drain liquidity at oracle prices or front-run restricted LPs. Config B constitutes broken core pool swap functionality: allowlisted users are locked out of the standard swap entry point, effectively freezing their ability to interact with the pool. Both impacts meet Sherlock High thresholds — Config A is a direct loss-of-principal path via unauthorized swap access; Config B is complete loss of core swap functionality for intended participants.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, public, documented swap entry point. No special privilege, admin action, malicious setup, or non-standard token is required. The bug is triggered on every router-mediated swap to any pool with `SwapAllowlistExtension` configured. Likelihood is High.

## Recommendation

The allowlist must gate the originating user, not the intermediary router. The preferred fix is to have the router encode `msg.sender` into `extensionData` before forwarding to the pool, and have `SwapAllowlistExtension.beforeSwap` decode and verify this value from `extensionData` rather than using the raw `sender` argument. This requires the extension to additionally verify that the caller is a trusted router. Alternatively, require allowlisted users to call the pool directly (bypassing the router), or deploy a router variant that enforces its own allowlist before calling the pool.

## Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension (beforeSwap order set)
  allowlist.setAllowedToSwap(pool, router_address, true)   // Config A: router allowlisted
  // alice is NOT individually listed

Attack:
  alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=alice, ...)          // MetricOmmSimpleRouter.sol L72-80
    → pool calls _beforeSwap(sender=router_address, ...)   // MetricOmmPool.sol L230-240
    → SwapAllowlistExtension checks allowedSwapper[pool][router_address] == true
    → hook returns selector (no revert)                    // SwapAllowlistExtension.sol L37-40
    → swap executes for alice despite alice not being allowlisted

Result:
  alice successfully swaps in a pool she is not authorized to access.
  The allowlist provides zero protection for any user routing through the router.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension` in `beforeSwapOrder`, call `setAllowedToSwap(pool, address(router), true)`, assert that an EOA not individually listed can successfully call `router.exactInputSingle()` and receive output tokens.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
