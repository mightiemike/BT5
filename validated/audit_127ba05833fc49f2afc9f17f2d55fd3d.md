Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address as `sender` instead of end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router is allowlisted — which is required for any allowlisted user to use the router — the allowlist is bypassed for all users, including those the pool admin explicitly excluded.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- this becomes `sender` in the extension
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks this value:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

At that point, `msg.sender` seen by the pool is the **router address**, so `sender` forwarded to the extension is the router, not the end user. If the pool admin allowlists the router (necessary for any allowlisted user to use the router), `allowedSwapper[pool][router] == true` passes for every caller regardless of their own allowlist status.

The `DepositAllowlistExtension` demonstrates the correct pattern — it checks `owner` (the economically relevant party) rather than `sender` (the payer/router):

```solidity
// DepositAllowlistExtension.sol L38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

No analogous second argument exists in `beforeSwap` that carries the true end-user identity; `recipient` is the output receiver, not necessarily the initiating user.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` for regulatory compliance, KYC gating, or market-maker restriction is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The attacker receives output tokens from a pool that was supposed to exclude them, exposing LP assets to adverse selection or regulatory violation from actors the pool admin explicitly blocked. This constitutes a broken core pool access-control mechanism causing direct exposure of LP principal — matching the "Admin-boundary break" and "Broken core pool functionality causing loss of funds" allowed impacts.

**Severity: High**

## Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap entry point and is publicly accessible to any EOA.
- Any pool admin who deploys `SwapAllowlistExtension` and allowlists the router (the natural and necessary setup for legitimate users to use the router) triggers the bypass automatically.
- No special timing, flash loan, privileged access, or non-standard token behavior is required.
- The bypass is permanent until the router is de-allowlisted, which simultaneously breaks all router-mediated swaps for legitimate users.

## Recommendation

The extension must check the economically relevant actor. Two sound approaches given the current interface:

1. **Gate by `recipient`**: The pool already passes `recipient` as the second argument to `beforeSwap`. For swap allowlists where the recipient is the intended gating identity, check `recipient` instead of `sender`. This mirrors how `DepositAllowlistExtension` checks `owner` rather than `sender`.

2. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before calling `pool.swap()`, and the extension decodes and checks it. This requires a trusted-router assumption and explicit documentation.

The fix pattern is already present in the codebase: `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (position owner) rather than `sender` (payer/router).

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin: setAllowedToSwap(pool, alice, true)
  - Pool admin: setAllowedToSwap(pool, router, true)
    (required so alice can use MetricOmmSimpleRouter)

Attack:
  1. bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})
  2. Router calls pool.swap(recipient=bob, ...) — msg.sender at pool == router
  3. Pool calls _beforeSwap(sender=router, recipient=bob, ...)
  4. ExtensionCalling._beforeSwap forwards sender=router to SwapAllowlistExtension.beforeSwap
  5. Extension checks: allowedSwapper[pool][router] == true  ✓  → no revert
  6. Swap executes — bob receives output tokens from a pool that was supposed to exclude him

Foundry test outline:
  - deployPool(extensions=[swapAllowlist])
  - swapAllowlist.setAllowedToSwap(pool, alice, true)
  - swapAllowlist.setAllowedToSwap(pool, address(router), true)
  - vm.prank(bob);
    router.exactInputSingle(ExactInputSingleParams({pool: pool, recipient: bob, ...}));
  - Assert: swap succeeds (no NotAllowedToSwap revert) despite bob not being allowlisted
  - Assert: bob receives output tokens
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
