Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router address, not the actual user. Any user can therefore bypass a curated pool's swap allowlist by routing through the public router, completely defeating the access-control guarantee the extension is meant to provide.

## Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that address against the allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call — not the original user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

The real payer (`msg.sender` of `exactInputSingle`) is stored only in transient storage for callback settlement via `_setNextCallbackContext` and is never surfaced to any extension. The same pattern applies to `exactInput` (all hops) and `exactOutputSingle`.

This creates an inescapable dilemma: if the router is allowlisted (required for allowlisted users to trade via the router), every non-allowlisted user can bypass the gate by calling `router.exactInputSingle(...)` instead of `pool.swap(...)` directly. If the router is not allowlisted, allowlisted users cannot use the router at all.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` loses its entire access-control guarantee the moment the router is added to the allowlist. Any address — including flash-loan-funded contracts, bots, or otherwise excluded parties — can execute swaps against the pool by routing through `MetricOmmSimpleRouter`. The pool's LP assets are exposed to actors the pool admin explicitly intended to exclude, constituting a broken core pool access-control mechanism with direct fund-impacting consequences.

## Likelihood Explanation

The router is the canonical, documented periphery entry point for swaps. Pool admins who want their allowlisted users to be able to trade will naturally add the router to the allowlist. The bypass requires no special knowledge, no privileged access, no special setup, and no non-standard tokens. Any user who observes that direct `pool.swap()` reverts can simply call the router instead.

## Recommendation

The extension must gate on the economically relevant actor, not the intermediate caller. Two viable approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` into `extensionData` for allowlist-gated pools, and have `SwapAllowlistExtension` decode and verify that address. The extension should revert if no valid user address is present in the payload.

2. **Recipient-based check**: Gate on the `recipient` parameter (the address that receives output tokens) rather than `sender`. For direct swaps the user is typically both sender and recipient; for router swaps the user is the recipient. This closes the most common bypass path, though it is imperfect for cases where sender and recipient differ.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so that allowlisted users can trade via the router)
  - Alice (address not in allowlist) wants to swap

Attack:
  1. Alice calls router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes successfully for Alice despite her not being allowlisted

Verification:
  - Alice calling pool.swap() directly → reverts NotAllowedToSwap
    (allowedSwapper[pool][alice] == false)
  - Alice calling router.exactInputSingle() → succeeds
    (allowedSwapper[pool][router] == true)
```