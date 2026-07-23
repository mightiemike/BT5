Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Curated-Pool Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool, which is always `msg.sender` of the `pool.swap` call — the immediate caller, not the end user. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract. If the pool admin allowlists the router to enable standard periphery access, every unprivileged user can bypass the curated-pool allowlist by calling through the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct for pool-namespacing) and `sender` is the first argument the pool passes when calling the extension. In `MetricOmmPool.swap`, the pool calls `_beforeSwap(msg.sender, recipient, ...)`, where `msg.sender` is whoever called `pool.swap` — the router, not the end user. `MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly, making the pool's `msg.sender` the router contract address. The extension therefore receives and checks `sender = router`, not `sender = end user`. The allowlist keyed `allowedSwapper[pool][swapper]` is intended to gate individual end users, but the router's address is what gets checked. Any admin who allowlists the router (the natural operational step to enable periphery access) inadvertently grants every unprivileged user the ability to swap in the curated pool.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is a curated pool where only approved addresses may trade. If the pool admin allowlists the router to enable normal periphery usage, any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and execute a full swap against the pool. The allowlist provides zero protection against router-routed swaps. Unauthorized users can receive pool output tokens at oracle prices, causing direct loss of LP principal and fee revenue. This is a direct bypass of curated-pool access control, qualifying as Critical/High impact under the allowed impact gate.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard swap entry point. Any pool admin who deploys a `SwapAllowlistExtension` and allowlists the router triggers the bypass automatically. No special knowledge, privileged access, or unusual conditions are required — any user calling `exactInputSingle` or `exactInput` through the router is affected. The bypass is deterministic and repeatable.

## Recommendation
The extension must check the actual economic actor, not the immediate pool caller. The preferred fix is to have the router encode the original `msg.sender` in `extensionData`, and have the extension decode and verify it against the allowlist. This requires a coordinated change to both `MetricOmmSimpleRouter` and `SwapAllowlistExtension`. Alternatively, the extension can check `recipient` (the second parameter to `beforeSwap`) and document that the allowlist gates who may receive output, though this is gameable if `recipient` can be set arbitrarily.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use the periphery.
4. Eve (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(params.recipient, ...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(router, ...)` via `_beforeSwap(msg.sender, ...)`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Eve successfully swaps in the curated pool, receiving output tokens she was never authorized to receive.

The allowlist invariant is broken: Eve is not in `allowedSwapper[pool]`, yet she executes a full swap and receives output tokens from the pool.