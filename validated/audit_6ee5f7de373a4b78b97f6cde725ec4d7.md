Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual End-User, Allowing Any User to Bypass the Per-Pool Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router contract becomes `msg.sender` of `pool.swap()`, not the actual end-user. If the pool admin allowlists the router — the only way to permit any router-mediated swap on a restricted pool — every unprivileged user can bypass the per-user allowlist by routing through the public router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the following check at L37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of `pool.swap()`.

`MetricOmmPool.sol` at L230–231 always passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← immediate caller of pool.swap(), not the end-user
    ...
);
```

`MetricOmmSimpleRouter.exactInputSingle` at L72–80 calls `pool.swap()` directly, making the router the `msg.sender` of the pool:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The router never forwards the actual end-user's address to the pool. The same pattern applies to `exactInput` (L104–112), `exactOutputSingle` (L136–137), and `exactOutput` (L165–181).

This creates an inescapable dilemma for the pool admin: not allowlisting the router causes all router-mediated swaps to revert even for allowlisted users; allowlisting the router neutralises the allowlist entirely, as any unprivileged address can call the public router and have the extension see only the allowlisted router address.

## Impact Explanation
`SwapAllowlistExtension` is the production mechanism for restricting pool access to specific counterparties (e.g., KYC'd institutions, whitelisted market makers). Once the router is allowlisted — which is required for any allowlisted user to access the pool via the standard periphery — the allowlist is completely neutralised. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps on a pool intended to be restricted. This is a direct admin-boundary break: an access control configured by the pool admin is bypassed by an unprivileged path through a public contract.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless contract. No special capital, flash loans, or MEV infrastructure is required. Any user who knows the router address can call it. The bypass is trivially reachable the moment the pool admin allowlists the router to enable normal periphery usage.

## Recommendation
The extension must verify the actual end-user identity, not the immediate caller of `pool.swap()`. The simplest safe fix: the router appends `abi.encode(msg.sender)` to `extensionData` before forwarding, and the extension decodes and checks that address when `sender` is a known trusted router. Alternatively, maintain a registry of trusted routers in the extension; when `sender` is a trusted router, read the actual user from a router-provided field in `extensionData`.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the intended grantee
  allowedSwapper[pool][router] = true         // required so alice can use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})
      → pool.swap(msg.sender=router, ...)
        → _beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  → passes
  bob's swap executes on the restricted pool.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
```

The existing `FullMetricExtensionTest` in `metric-periphery/test/extensions/FullMetricExtension.t.sol` tests direct pool calls only and does not exercise the router path, so the bypass is not caught by the existing test suite.