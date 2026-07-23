Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Non-Allowlisted Users to Bypass Swap Restrictions via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to support router-mediated swaps, every user — including non-allowlisted ones — can bypass the per-user allowlist by routing through the router, breaking the core access-control invariant of curated pools.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the contract that calls the extension) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap()` call.

In `MetricOmmPool.swap`, the pool calls `_beforeSwap(msg.sender, ...)`, passing its own `msg.sender` as `sender`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making the pool's `msg.sender` the router address. The router stores the original user as the payer in transient storage (for the payment callback via `_setNextCallbackContext`), but the `sender` forwarded to the extension is the **router address**, not the end user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable split: if the pool admin allowlists the router to support normal UX, all users bypass the allowlist. If the router is not allowlisted, all router-mediated swaps are blocked even for allowlisted users. There is no configuration that simultaneously allows allowlisted users to swap through the router while blocking non-allowlisted users.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to known counterparties (e.g., KYC-verified market makers, whitelisted institutions, or to exclude known MEV bots) loses that protection the moment the router is allowlisted. Any unprivileged user can call `router.exactInputSingle` or `router.exactInput` and trade against the pool's liquidity. This breaks the core access-control invariant of the curated pool and exposes LP funds to unauthorized extraction by actors the pool admin explicitly intended to exclude. This constitutes a broken core pool functionality causing potential loss of funds and a direct admin-boundary break where the allowlist policy is bypassed by an unprivileged path through the supported periphery.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps, providing slippage protection, multi-hop routing, and deadline checks. A pool admin who wants to support normal UX must allowlist the router. The bypass is therefore reachable by any user on any curated pool that supports router-mediated swaps — a standard production configuration. No special privileges or unusual conditions are required; any unprivileged user can exploit this by simply calling the public router functions.

## Recommendation
The extension must check the **original end user**, not the intermediary router. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. The pool admin must configure the extension to trust this field only when `sender == router`.

2. **Trusted router registry**: The extension maintains a registry of trusted routers; when `sender` is a trusted router, it reads the actual user from `extensionData`; otherwise it checks `sender` directly.

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin allowlists Alice: extension.setAllowedToSwap(pool, alice, true)
3. Pool admin allowlists the router (to support router UX):
       extension.setAllowedToSwap(pool, router, true)
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, ...) — pool's msg.sender = router.
6. Pool calls _beforeSwap(router, bob, ...) → extension.beforeSwap(router, bob, ...)
7. Extension checks allowedSwapper[pool][router] → true → PASSES.
8. Bob's swap executes against the curated pool's liquidity.
   Bob was never allowlisted; the guard is fully bypassed.
```

Foundry test plan: Deploy a pool with `SwapAllowlistExtension` as the `beforeSwap` hook. Allowlist Alice and the router. Confirm Bob (not allowlisted) can successfully call `router.exactInputSingle` and receive output tokens, while a direct `pool.swap` call from Bob reverts with `NotAllowedToSwap`.