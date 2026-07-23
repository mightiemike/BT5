Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. Any pool that allowlists the router (required for router-mediated swaps to work) unconditionally allows every user—including explicitly excluded ones—to bypass the allowlist.

## Finding Description

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the `sender` argument:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap(), i.e. the router
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router address. The check becomes `allowedSwapper[pool][router]`, never touching the end user's address.

`MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` all call `pool.swap(recipient, ...)` directly without forwarding the originating user as a separate parameter:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

This creates an irreconcilable dilemma: if the router is not allowlisted, no allowlisted user can use the router; if the router is allowlisted, every user bypasses the allowlist. The `DepositAllowlistExtension` avoids this by checking `owner`—an explicit parameter passed separately from `msg.sender` in `addLiquidity`—which correctly identifies the economic actor regardless of intermediary. The swap path has no equivalent separate parameter for the originating user.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set (e.g., KYC'd addresses, whitelisted market makers) is fully bypassed by any user routing through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps at oracle-derived prices that LPs only consented to provide to specific counterparties, resulting in direct loss of LP principal and a broken core pool invariant (curated access control). This is a high-severity broken core pool functionality causing loss of funds.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public entry point for swaps. Any pool enabling `SwapAllowlistExtension` that also wants to support router-mediated swaps for its allowlisted users must allowlist the router address, at which point the bypass is unconditional and requires no special privileges. The attacker only needs to call the public router with a valid swap path. No setup beyond the pool admin's own required configuration is needed.

## Recommendation

The pool should pass the originating user as `sender` to extensions rather than its own `msg.sender`. The `swap` interface should accept an explicit `sender` parameter (analogous to `owner` in `addLiquidity`), and `MetricOmmSimpleRouter` should pass `msg.sender` (the end user) as that argument. Alternatively, `SwapAllowlistExtension.beforeSwap` could decode the true caller from a trusted `extensionData` payload signed or set by the router. The `DepositAllowlistExtension` pattern—checking an explicit `owner` parameter rather than the forwarded `sender`—is the correct model.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin: setAllowedToSwap(pool, router, true)   // required for any router swap
  - Pool admin: setAllowedToSwap(pool, alice, true)    // alice is intended allowlisted user
  - Pool admin does NOT allowlist bob

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool, recipient=bob, ...})
  2. Router calls pool.swap(recipient=bob, ...)          // MetricOmmSimpleRouter.sol L72-80
  3. Pool sets sender = msg.sender = router              // MetricOmmPool.sol L231
  4. Pool calls _beforeSwap(sender=router, ...)
  5. Extension checks allowedSwapper[pool][router] → true ✓
  6. Swap executes — bob trades on a pool designed to exclude him

Result:
  - allowedSwapper[pool][bob] is false
  - bob successfully swapped via the router
  - The allowlist invariant is broken for every non-allowlisted user
```