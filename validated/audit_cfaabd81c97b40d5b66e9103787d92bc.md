Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of `pool.swap`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to enable standard periphery UX inadvertently grants every network participant unrestricted swap access, defeating the per-user allowlist entirely.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router `msg.sender` inside `pool.swap`: [3](#0-2) 

The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. Once the router is allowlisted (the natural and expected admin action to enable periphery UX), the check passes for every caller of the router regardless of whether that caller is individually allowlisted. The check `allowedSwapper[pool][bob]` is never evaluated; the extension only sees the router's address.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized swaps execute at the oracle-derived bid/ask price, draining LP inventory and generating protocol fees from trades the pool admin explicitly intended to block. This is a direct, fund-impacting breach of the access-control invariant: LP assets and owed fees are exposed to unrestricted trading.

## Likelihood Explanation

The trigger is fully unprivileged. Any EOA can call `MetricOmmSimpleRouter.exactInputSingle` with a valid pool address. The only precondition — that the pool admin has allowlisted the router — is the natural and expected configuration for any pool that wants to support the standard periphery UX. No malicious setup, non-standard token, or admin collusion is required. The bypass is repeatable on every swap through the router.

## Recommendation

The extension must identify the actual end-user, not the direct caller of `pool.swap`. The cleanest fix is to have the router append `abi.encode(msg.sender)` to the `extensionData` it forwards to the pool. `SwapAllowlistExtension.beforeSwap` should decode that address from `extensionData` when it is non-empty and check it against the allowlist, falling back to `sender` for direct pool calls. Pool admins then allowlist EOAs, not the router. This requires a coordinated change to `MetricOmmSimpleRouter` and `SwapAllowlistExtension`.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is added so Alice can use the standard periphery.
4. Bob (not on the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender == router`.
6. `MetricOmmPool.swap` passes `router` as `sender` to `_beforeSwap`.
7. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes at the oracle price. `allowedSwapper[pool][bob]` is never evaluated. [5](#0-4) [6](#0-5)

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
