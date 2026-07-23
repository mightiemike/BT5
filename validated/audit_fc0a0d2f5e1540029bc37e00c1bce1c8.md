Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the `msg.sender` of the pool call — the immediate caller, not the originating user. When swaps are routed through `MetricOmmSimpleRouter`, the router's address is presented as `sender`. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously grants every address on-chain the ability to bypass the curated-pool gate, because the extension never observes the end-user's address at all.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- immediate caller, not originating user
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whatever the pool forwarded. When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`), the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

So `msg.sender` inside `pool.swap` is the router's address. The extension then evaluates `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (the only mechanism to permit router-mediated swaps for legitimate users), this check passes for **every caller** regardless of whether that individual user is on the allowlist, because the extension never sees the user's address.

Direct call path: `user → pool.swap()` → `sender = user` → `allowedSwapper[pool][user]` checked correctly.  
Router path: `user → router → pool.swap()` → `sender = router` → `allowedSwapper[pool][router]` checked — passes for all users when router is allowlisted.

## Impact Explanation
Any user not individually allowlisted can trade on a curated pool by routing through `MetricOmmSimpleRouter` when the router is allowlisted. This is a direct bypass of the pool's access-control policy. Curated pools restrict trading to known counterparties (KYC'd users, protocol-owned addresses, specific market makers). Unauthorized swaps drain LP-owned liquidity and generate fees from actors the pool was explicitly designed to exclude. This constitutes broken core pool functionality and direct loss of LP principal through unauthorized execution — matching the "Broken core pool functionality causing loss of funds" and "Admin-boundary break" allowed impact categories.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical user-facing swap entry point. A pool admin who deploys a curated pool and wants allowlisted users to use the standard router **must** allowlist the router — there is no other mechanism to enable router-mediated swaps. Once the router is allowlisted, the bypass is immediately available to any address with no additional preconditions, no privileged access, and no special token behavior. The scenario is a natural and expected operational configuration.

## Recommendation
The extension must resolve the actual end-user address rather than trusting the `sender` argument when the immediate caller is a known periphery contract:

1. **Pass-through identity**: Require the router to forward the originating user address in `extensionData`, and have `SwapAllowlistExtension` decode and check that address when `sender` is a recognized router.
2. **Transient-storage identity**: Have the router write the originating user into transient storage before calling the pool, and have the extension read it from there (the router already uses transient storage for callback context).
3. **Direct-only policy**: Document and enforce at the factory level that allowlisted pools must not allowlist any router or intermediary, rejecting extension configurations that combine `SwapAllowlistExtension` with a router allowlist entry.

The root fix is that `allowedSwapper` must be keyed on the economically responsible actor (the end user), not the immediate `msg.sender` of the pool call.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured (allowAllSwappers = false).
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router usage
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is allowlisted
  - bob is NOT allowlisted.

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient=bob, ...) — msg.sender in pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true ✓
  5. Swap executes for bob despite bob not being allowlisted.

Direct call check (for comparison):
  1. bob calls pool.swap(...) directly
  2. Pool calls _beforeSwap(sender=bob, ...)
  3. SwapAllowlistExtension checks allowedSwapper[pool][bob] → false → revert ✓

Foundry test outline:
  - deployPool(extensions=[swapAllowlistExtension])
  - swapAllowlistExtension.setAllowedToSwap(pool, router, true)
  - vm.prank(bob); router.exactInputSingle(pool, ...)
  - assert swap succeeds (no revert)
  - vm.prank(bob); pool.swap(...) directly
  - assert swap reverts with NotAllowedToSwap
```