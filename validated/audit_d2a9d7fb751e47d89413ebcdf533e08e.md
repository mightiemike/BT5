Audit Report

## Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` — Any User Can Swap on Curated Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the pool's `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller, so the extension checks whether the **router** is allowlisted rather than the **original user**. If the pool admin allowlists the router to enable permitted users to trade through it, every unpermissioned user can bypass the allowlist by routing through the same public contract.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the check at L37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called externally by the pool via `CallExtension.callExtension`) and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap` at L230–231, the pool passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    ...
);
```

`ExtensionCalling._beforeSwap` at L162–176 forwards that value unchanged via `abi.encodeCall`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly at L72–80:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The pool's `msg.sender` is the **router address**, so the extension receives `sender = router`. The original user's address is never visible to the extension. The same applies to `exactInput` (all hops at L104), `exactOutputSingle` (L136), and `exactOutput` recursive hops (L165, L220).

This creates an inescapable dilemma for the pool admin:
- **Do not allowlist the router**: allowlisted users cannot use the router at all — broken core functionality.
- **Allowlist the router**: every user can bypass the allowlist by routing through the router.

## Impact Explanation
A pool deploying `SwapAllowlistExtension` is a curated pool where only specific addresses may trade (e.g., KYC'd counterparties or protocol-controlled addresses). Once the pool admin allowlists the router to restore normal UX for permitted users, any unpermissioned address can call `exactInputSingle` through the router and the extension's check passes because `allowedSwapper[pool][router] == true`. The allowlist is completely nullified — unauthorized users can swap on the curated pool, bypassing the curation policy entirely. This constitutes broken core pool functionality and direct loss of the access-control invariant that the extension is designed to enforce.

## Likelihood Explanation
The router is the standard, documented periphery entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router (the expected UX) must allowlist the router. The bypass is reachable on any production curated pool that has not deliberately blocked all router access. No special privileges, flash loans, or multi-block setup are required — a single `exactInputSingle` call suffices.

## Recommendation
The extension must gate the **original user**, not the intermediary. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the router to cooperate and the extension to trust the encoding.
2. **Simplest safe fix**: Document that the router must never be allowlisted and that allowlisted users must call the pool directly. This is a severe UX restriction but preserves the invariant.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is permitted
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router allowlisted so alice can use it

Attack (by bob, who is NOT allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: curated_pool,
         recipient: bob,
         ...
     })
  2. Router calls pool.swap(...) — pool's msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] == true → passes
  5. Swap executes; bob receives tokens from the curated pool

Result: bob, a non-allowlisted user, successfully swaps on a curated pool.
        The allowlist is completely bypassed.
```