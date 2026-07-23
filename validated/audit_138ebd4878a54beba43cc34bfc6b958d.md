Audit Report

## Title
SwapAllowlistExtension checks router address instead of end-user, making per-user allowlist permanently bypassable for router-mediated swaps — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap`. When any user routes through `MetricOmmSimpleRouter`, `sender` equals the router contract address, not the actual trader. This creates two mutually exclusive failure modes with no correct configuration: either allowlisted users cannot use the router at all, or the router is allowlisted and any unprivileged user can bypass the restriction entirely.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // direct caller of pool.swap
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against its per-pool mapping:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInputSingle` is called, the router is the direct caller of `pool.swap`:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

So `sender` arriving at the extension is always the router's address, never the end-user's address. The same applies to `exactInput`, `exactOutputSingle`, and the recursive callback path in `exactOutput` (L220-228), where subsequent pools are also called from the router.

**Mode A — allowlist individual users, not the router:** Pool admin calls `setAllowedToSwap(pool, Alice, true)`. Alice calls `router.exactInputSingle`. The extension checks `allowedSwapper[pool][router]` → false → reverts. Alice (legitimately allowlisted) cannot use the router.

**Mode B — allowlist the router to restore router access:** Pool admin calls `setAllowedToSwap(pool, router, true)`. Bob (not allowlisted) calls `router.exactInputSingle`. The extension checks `allowedSwapper[pool][router]` → true → passes. Bob bypasses the allowlist entirely.

There is no configuration that simultaneously allows allowlisted users to swap through the router and blocks non-allowlisted users.

## Impact Explanation

This is an admin-boundary break reachable by any public caller. Any unprivileged address can bypass a pool's swap allowlist restriction by routing through `MetricOmmSimpleRouter` once the pool admin is forced to allowlist the router to restore legitimate user access. Additionally, the primary user-facing swap interface is rendered unusable for any pool deploying `SwapAllowlistExtension` with per-user allowlisting, constituting broken core swap functionality.

## Likelihood Explanation

Every pool that deploys `SwapAllowlistExtension` with the intent to restrict swaps to specific users is affected. The bypass requires only a standard call to `MetricOmmSimpleRouter.exactInputSingle` — no special setup, no flash loans, no privileged access. The pool admin cannot detect or prevent the bypass without removing the router from the allowlist, which re-breaks legitimate router access for allowlisted users.

## Recommendation

The extension must check the actual end-user, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks that address. This requires a trusted router or a signed attestation.
2. **Separate allowlist entries for direct vs. router-mediated swaps**: Document clearly that the allowlist only gates direct `pool.swap` callers and provide a companion extension that reads the real user from `extensionData` for router paths.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin: setAllowedToSwap(pool, Alice, true)
   — Alice is the only allowlisted swapper.

3. Alice calls router.exactInputSingle({pool: pool, tokenIn: T0, ...})
   → router calls pool.swap(recipient, ...)
   → pool calls _beforeSwap(router, ...)
   → extension checks allowedSwapper[pool][router] → false
   → reverts NotAllowedToSwap
   Alice (allowlisted) cannot use the router. ✗

4. Pool admin: setAllowedToSwap(pool, router, true)
   — Admin adds router to restore Alice's router access.

5. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(recipient, ...)
   → pool calls _beforeSwap(router, ...)
   → extension checks allowedSwapper[pool][router] → true
   → passes
   Bob (not allowlisted) swaps successfully. ✗
```