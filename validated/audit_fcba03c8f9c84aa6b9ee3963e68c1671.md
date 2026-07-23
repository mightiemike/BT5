Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual end-user swapper, allowing allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`MetricOmmPool.swap` passes its own `msg.sender` (the router contract) as the `sender` argument to `_beforeSwap`, which forwards it to `SwapAllowlistExtension.beforeSwap`. The extension then evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. If the router is allowlisted (a natural admin action to enable router-mediated swaps), every user — including those explicitly excluded — can bypass the per-pool allowlist by routing through the public `MetricOmmSimpleRouter`.

## Finding Description

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the `sender` argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- the immediate caller, i.e. the router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension hook:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The actual end-user's address is stored only in transient storage via `_setNextCallbackContext` for the payment callback and is never forwarded to the pool as the swap initiator:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The pool sees `msg.sender = router`, so the extension resolves `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. There is no mechanism in the extension or the pool to recover the original transaction initiator.

## Impact Explanation

**Allowlist bypass (High):** Any user — including those the pool admin explicitly excluded — can swap on a curated pool by routing through the public `MetricOmmSimpleRouter`. The allowlist policy is completely defeated. Disallowed counterparties can trade against LP funds, violating the pool's curation invariant and causing direct LP loss on pools designed for restricted counterparties. This matches "Broken core pool functionality causing loss of funds" and "Admin-boundary break: pool admin exceeds caps, bypasses timelocks, or factory/oracle role checks are bypassed by an unprivileged path."

**Broken swap flow (Medium):** If the router is not allowlisted, every allowlisted user who uses the standard router has their swap reverted, making the core swap flow unusable for the intended participants.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Pool admins configuring an allowlist will naturally expect it to gate the actual user. The bypass is reachable by any unprivileged user with zero preconditions beyond routing through the public router. No special tokens, flash loans, or admin access are required. The precondition (router being allowlisted) is the natural admin action when the router is the intended entry point.

## Recommendation

The pool should forward the original transaction initiator — not the immediate `msg.sender` — as the `sender` argument to extension hooks. One approach: the router encodes the real user address in `extensionData` and the extension reads it from there (with a trusted-router check). A cleaner approach is for the pool to accept an explicit `sender` override from trusted periphery contracts, or for the extension to read the real payer from a standardized field in `extensionData` rather than trusting the raw `sender` argument when the caller is a known router.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps.
3. Pool admin calls `setAllowedToSwap(pool, userA, true)` to allowlist a specific user; `userB` is intentionally excluded.
4. `userB` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
6. The pool calls `_beforeSwap(router, recipient, ...)` → `extension.beforeSwap(router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` (router was allowlisted in step 2).
8. The swap executes for `userB`, bypassing the allowlist entirely.

Foundry test plan: deploy `SwapAllowlistExtension`, configure a pool with it, allowlist the router but not `userB`, call `exactInputSingle` as `userB`, and assert the swap succeeds (demonstrating the bypass).