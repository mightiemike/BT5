Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to `SwapAllowlistExtension.beforeSwap`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. Any pool admin who allowlists the router (the only way to let allowlisted users use the standard router) simultaneously opens the pool to all public users.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` receives that value and checks it against the per-pool allowlist, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the pool's `msg.sender` the router contract: [3](#0-2) 

This creates an irreconcilable conflict: if the pool admin does **not** allowlist the router, allowlisted users cannot swap through the router (reverts with `NotAllowedToSwap`). If the admin **does** allowlist the router (the only way to enable router-based swaps for allowlisted users), `allowedSwapper[pool][router] = true` causes the check to pass for **any** caller who routes through the router, completely bypassing per-user access control. [4](#0-3) 

## Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., a private OTC pool, a KYC-gated pool) can be fully opened to arbitrary public swappers the moment the router is allowlisted. Unauthorized swappers can drain liquidity at oracle-quoted prices, extract value from LP positions, or trigger downstream extension state in ways the pool admin did not intend. This constitutes broken core pool functionality causing direct loss of user principal and LP assets.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who wants allowlisted users to use the standard router UI will naturally add the router to the allowlist. The bypass is then immediately available to all public users with no further preconditions. The trigger is a routine, expected admin action, not an exotic configuration.

## Recommendation

The extension must receive and check the original end-user address, not the intermediary's address. Two complementary fixes:

1. **Router-level**: `MetricOmmSimpleRouter` should pass the actual end user (`msg.sender` at router entry) as `extensionData` so extensions can verify the real initiator.
2. **Extension-level**: `SwapAllowlistExtension.beforeSwap` should decode the real user from `extensionData` when `sender` is a known router, or the pool interface should propagate a trusted-forwarder identity through the call chain.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice should be able to swap.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the standard router UI.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. Router calls `pool.swap(recipient=Bob, ...)` — pool's `msg.sender` = router.
6. Pool calls `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps on the restricted pool, bypassing the per-user allowlist entirely.

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
