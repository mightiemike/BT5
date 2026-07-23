Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of `pool.swap()`, so the extension checks whether the router is allowlisted rather than the actual end-user. Any unpermitted address can bypass the per-pool allowlist by calling the public router if the router itself is allowlisted.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmPool.swap()` passes `msg.sender` (the direct caller of the pool) as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes that value verbatim into the extension call:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` of the pool:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The result is that `sender` arriving at the extension is the router address, not the end-user. The allowlist lookup `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][user]`. No existing guard in the extension, pool, or router resolves the original EOA caller.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses is fully bypassed once the router is allowlisted. Any unpermitted address can call `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` and the extension will pass the check because it sees the allowlisted router, not the blocked user. This constitutes broken core pool functionality: the allowlist extension fails to enforce its intended access control, allowing unauthorized swappers to execute swaps the pool designer intended to block and potentially drain LP value.

## Likelihood Explanation
The scenario is directly reachable by any unprivileged user. The only precondition is that the pool admin has allowlisted the router — a natural and expected operational step whenever the admin wants legitimate users to use the standard periphery router. No special role, no malicious setup, and no non-standard token is required. The router is a public, immutable contract that any EOA can call. The same bypass applies to all four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

## Recommendation
The extension must resolve the ultimate user rather than the direct pool caller. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` (the end-user) into `extensionData`; the extension decodes and checks that address. This requires a convention between router and extension.
2. **Check `recipient` instead of (or in addition to) `sender`**: For swap allowlists the economically relevant actor is often the recipient of output tokens; gating on `recipient` is harder to spoof via router indirection.
3. **Dedicated router-aware allowlist**: Extend the extension to accept a `(router, user)` allowlist entry so the router can attest the real caller.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as extension1, beforeSwap order = extension1
  - allowedSwapper[pool][router] = true   (admin allowlists the router)
  - allowedSwapper[pool][alice]  = true   (alice is a permitted user)
  - allowedSwapper[pool][bob]    = false  (bob is NOT permitted)

Attack:
  1. bob calls router.exactInputSingle({pool: pool, ...})
  2. router calls pool.swap(recipient=bob, ...)
  3. pool calls _beforeSwap(sender=router, ...)
  4. ExtensionCalling encodes sender=router into beforeSwap call
  5. extension checks allowedSwapper[pool][router] == true → passes
  6. bob's swap executes; allowlist is bypassed

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
```