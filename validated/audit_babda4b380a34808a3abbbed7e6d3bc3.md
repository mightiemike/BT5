Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any address to bypass per-pool swap allowlists via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` receives `sender` from the pool, which is set to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. Any pool admin who allowlists the router to permit router-mediated swaps for their approved users simultaneously grants unrestricted swap access to every address that calls the router.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` forwards that value verbatim to every configured extension via `abi.encodeCall`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap()` then checks that `sender` against the per-pool allowlist, where `msg.sender` is the pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle()` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly with the router as `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

At this point `msg.sender` inside `pool.swap()` is the router contract, so `sender = router`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. A pool admin who wants allowlisted users to swap through the router must add the router to the allowlist. Once the router is allowlisted, the check trivially passes for every caller because the router is always the immediate caller of `pool.swap()`. The allowlist is completely bypassed for all four router entry points.

## Impact Explanation
A pool admin deploys a curated pool (e.g., KYC-only, market-maker-only) and configures `SwapAllowlistExtension` to restrict swaps to a specific set of addresses. Any address outside that set can bypass the restriction by calling any of the four `MetricOmmSimpleRouter` swap functions. The router is a public, permissionless contract. The bypass requires no special privilege and no token approval beyond the normal swap approval. Every swap executed on a curated pool by a non-allowlisted user is a direct policy violation: the pool's liquidity is consumed by actors the pool admin explicitly excluded, constituting a broken core pool access-control invariant with direct economic consequences for the pool and its LPs.

## Likelihood Explanation
The bypass is only reachable when the pool admin has allowlisted the router address. However, allowlisting the router is the only way to let allowlisted users trade through the router — a standard, documented periphery path. Any pool admin who wants to support router-mediated swaps for their allowlisted users is forced to allowlist the router, which simultaneously opens the bypass to everyone. The condition is a natural consequence of normal pool configuration, not an exotic mistake. The bypass is repeatable, requires no front-running, and is available to any EOA or contract.

## Recommendation
The extension must gate the original economic actor, not the immediate caller of `pool.swap()`:

1. **Router-side fix**: `MetricOmmSimpleRouter` already stores the originating `msg.sender` in transient storage via `_setNextCallbackContext`. Expose it via a public getter. `SwapAllowlistExtension.beforeSwap()` can then call back to the router (identified via `sender`) to retrieve the real initiator and check that address against the allowlist.

2. **Extension-side fix**: Extend the `beforeSwap` interface with a distinct `initiator` field populated by the pool from a trusted source (e.g., transient storage set by the router before calling `pool.swap()`), separate from `sender`.

3. **Minimum guard**: Add explicit NatSpec to `SwapAllowlistExtension` stating that allowlisting the router grants swap access to all router users, so pool admins are not misled into believing per-user restrictions still apply.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  - Pool admin calls setAllowedToSwap(pool, router, true)  // required for alice to use the router
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension evaluates: allowedSwapper[pool][router] == true → passes
  5. bob's swap executes successfully on the curated pool

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist fully bypassed

Foundry test outline:
  - Deploy SwapAllowlistExtension, configure pool with it
  - setAllowedToSwap(pool, router, true); setAllowedToSwap(pool, alice, true)
  - vm.prank(bob); router.exactInputSingle(...) → assert no revert
  - vm.prank(bob); pool.swap(...) directly → assert revert NotAllowedToSwap()
```