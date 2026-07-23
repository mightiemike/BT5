Audit Report

## Title
`SwapAllowlistExtension` checks router address as swapper, enabling full allowlist bypass when router is allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()` — the immediate caller, not the originating user. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address. Pool admins who allowlist the router to support allowlisted users inadvertently grant swap access to every address, completely defeating the allowlist guard.

## Finding Description
In `MetricOmmPool.swap`, `msg.sender` (the immediate caller of `pool.swap()`) is forwarded as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
```

`ExtensionCalling._beforeSwap` encodes this as the first argument to the extension call:

```solidity
// ExtensionCalling.sol L162-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks this `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` with `msg.sender = router`:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The pool therefore passes `sender = router` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The router stores the originating `msg.sender` only in transient storage for the payment callback; it is never surfaced to the extension. There is no existing guard that recovers the originating user identity in the extension path.

**Exploit flow:**
1. Pool admin deploys pool with `SwapAllowlistExtension` and allowlists `userA`: `setAllowedToSwap(pool, userA, true)`.
2. `userA` reports swap failures through the router (because `allowedSwapper[pool][router]` is false).
3. Pool admin allowlists the router: `setAllowedToSwap(pool, router, true)`.
4. `userB` (never allowlisted) calls `router.exactInputSingle({pool: pool, recipient: userB, ...})`.
5. Router calls `pool.swap(userB, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, userB, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → passes.
8. `userB` successfully swaps on the curated pool, bypassing the individual allowlist entirely.

## Impact Explanation
Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle` and successfully swap on a pool protected by `SwapAllowlistExtension`, once the pool admin has allowlisted the router. The allowlist guard — the sole access control mechanism for curated pools (KYC-gated, institutional-only, whitelist-controlled) — is completely bypassed. Unauthorized users can execute swaps at oracle-derived prices on pools that were intended to be restricted, causing direct loss of LP principal through unfavorable or unauthorized trades. This is a broken core pool protection with direct fund impact.

## Likelihood Explanation
Pool admins who deploy `SwapAllowlistExtension` to gate specific users will naturally encounter the router-blocking problem when allowlisted users report swap failures through the router. The intuitive fix — allowlisting the router — silently opens the pool to all users. The bypass requires no special privileges: any public address can call `exactInputSingle` on the router. The router is a supported periphery contract, making this a reachable path on every curated pool that also supports router-based swaps. The admin action that triggers the vulnerability is the expected operational response to a user-reported issue.

## Recommendation
The extension must check the originating user, not the immediate caller of `pool.swap()`. Two options:

1. **Pass originating user via `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; `SwapAllowlistExtension` decodes and checks it when present, falling back to `sender` for direct pool calls.
2. **Check `recipient` instead of `sender`**: For swap allowlists, gating the `recipient` (the address that receives output tokens) is often the economically relevant actor. The router passes `params.recipient` directly, which is the actual user's chosen destination.

The cleanest fix is option 1: the router encodes the originating user in `extensionData`, and `SwapAllowlistExtension` reads it when present.

## Proof of Concept
1. Deploy pool with `SwapAllowlistExtension` configured.
2. Call `setAllowedToSwap(pool, userA, true)` — allowlist `userA`.
3. `userA` calls `router.exactInputSingle({pool: pool, ...})` → reverts because `allowedSwapper[pool][router]` is false.
4. Pool admin calls `setAllowedToSwap(pool, router, true)` to fix `userA`'s issue.
5. `userB` (never allowlisted) calls `router.exactInputSingle({pool: pool, recipient: userB, ...})`.
6. Router calls `pool.swap(userB, ...)` with `msg.sender = router`.
7. Pool calls `_beforeSwap(router, userB, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap succeeds.
8. Assert: `userB` received output tokens despite never being allowlisted.

Foundry test: deploy `SwapAllowlistExtension`, configure pool, execute steps 2–8 with `vm.prank(userB)` on the router call, assert swap succeeds and `allowedSwapper[pool][userB]` is still `false`.