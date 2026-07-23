Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` sets to `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. A pool admin who allowlists the router to support standard UX inadvertently grants every user — including explicitly excluded ones — the ability to bypass the allowlist by routing through the router.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the direct pool caller) is allowlisted: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, passing `""` as `extensionData` — the original `msg.sender` (end user) is never encoded or forwarded: [3](#0-2) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput` — none encode the originating user into `extensionData`. [4](#0-3) 

When the router calls `pool.swap()`, `sender` inside the extension equals the router's address. The check `allowedSwapper[pool][router] == true` passes for every caller of the router, including users the admin explicitly excluded. There is no mechanism in the extension to recover the original end user's address.

## Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to a specific set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. A disallowed user can execute swaps at live oracle prices, extracting value the allowlist was designed to prevent. This constitutes a direct bypass of an admin-configured access control, resulting in direct loss of LP principal and protocol fees above Sherlock thresholds. Severity: **High**.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point. Any pool admin who deploys a curated pool and also wants to support the standard router UX will naturally allowlist the router. The bypass requires no special privileges, no non-standard tokens, and no malicious setup — only a standard router call from any EOA. The condition is a natural and expected operational state.

## Recommendation

The router must encode the original `msg.sender` into `extensionData` so the extension can verify the true end user. Concretely:

1. **Router-side**: Encode `msg.sender` into `extensionData` before calling `pool.swap()`.
2. **Extension-side**: In `beforeSwap`, when `sender` is a known/trusted router, decode `extensionData` to extract the forwarded user address and check `allowedSwapper[pool][forwardedUser]` instead of `allowedSwapper[pool][sender]`.

Until fixed, pool admins must be warned that allowlisting the router is functionally equivalent to calling `setAllowAllSwappers(pool, true)`.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` as the `beforeSwap` hook.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowed swapper.
3. Admin calls `setAllowedToSwap(pool, router, true)` — to let Alice use the standard router UI.
4. Bob (explicitly not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(recipient=Bob, ...)` with `msg.sender = router`.
6. `_beforeSwap` is called with `sender = router`.
7. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes. The allowlist is bypassed with no special privileges required.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
```
