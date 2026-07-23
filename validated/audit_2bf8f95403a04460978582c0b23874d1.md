Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Original Caller, Allowing Any User to Bypass Per-User Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender` at swap time. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original EOA. If the pool admin allowlists the router (required for any router-mediated swap to work for legitimate users), every unprivileged user can bypass the per-user allowlist by routing through the router.

## Finding Description
In `MetricOmmPool.swap`, the pool calls `_beforeSwap(msg.sender, ...)` where `msg.sender` is whoever called `pool.swap()`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // pool's msg.sender — the router, not the original EOA
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this `sender` argument directly to the extension:

```solidity
// ExtensionCalling.sol L149-177
_callExtensionsInOrder(BEFORE_SWAP_ORDER, abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...)));
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check becomes `allowedSwapper[pool][router]`.

When `MetricOmmSimpleRouter.exactInputSingle` is called, it calls `pool.swap(params.recipient, ...)` directly with no mechanism to forward the original `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The pool admin faces an impossible choice: not allowlisting the router blocks all router-mediated swaps (including for legitimately allowlisted users), while allowlisting the router grants every unprivileged address the ability to swap on the restricted pool by calling any of `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., KYC-gated, institutional, or protocol-internal pools) has its access control rendered entirely ineffective for any user routing through `MetricOmmSimpleRouter`. Any unprivileged address can execute swaps on a restricted pool, draining pool liquidity or extracting value in ways the pool admin explicitly intended to prevent. This is a broken core pool functionality / admin-boundary break: the `allowedSwapper` registry entry for individual users is the exact wrong value — it is never consulted for router-mediated swaps; only `allowedSwapper[pool][router]` is checked.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public periphery contract with no access controls. Any user can call it at any time. The bypass requires only that the pool admin has allowlisted the router, which is a necessary operational step for any legitimate router-mediated swap to function. The trigger is fully unprivileged and requires no special setup beyond the pool's own intended configuration.

## Recommendation
The extension must gate on the original user identity, not the intermediary. Two viable approaches:

1. **Pass the original caller via `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`; the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Trusted-forwarder pattern with transient storage**: The router writes the original caller to a transient storage slot before calling the pool; the extension reads that slot when it detects `sender` is the router. This avoids modifying `extensionData` but requires the extension to trust the router.

3. **Document the limitation**: Explicitly document that `SwapAllowlistExtension` only enforces per-user access for direct pool calls, and that router-mediated swaps must be blocked by not allowlisting the router, requiring allowlisted users to call the pool directly.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)` so Alice can use the router.
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: restrictedPool, ...})`.
4. The router calls `pool.swap(recipient, ...)` — pool's `msg.sender` is the router.
5. Pool calls `_beforeSwap(router, ...)` → extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Bob successfully swaps on the restricted pool, bypassing the per-user allowlist entirely.