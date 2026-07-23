Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. A pool admin who allowlists the router to support standard UX inadvertently opens the gate for every user on-chain, completely defeating the allowlist.

## Finding Description

**Root cause — wrong actor bound in `SwapAllowlistExtension.beforeSwap`:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap` (metric-core/contracts/MetricOmmPool.sol, L230–240). `ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension (metric-core/contracts/ExtensionCalling.sol, L149–177). `SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension = the pool):

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

**Router path — user identity is lost:**

`MetricOmmSimpleRouter.exactInputSingle` stores the actual user's address only in transient storage for the payment callback (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`), then calls `pool.swap()` directly (metric-periphery/contracts/MetricOmmSimpleRouter.sol, L71–86). The router is `msg.sender` to the pool; the actual user's address is never forwarded as `sender`. The same applies to `exactInput` (L103–112), `exactOutputSingle` (L135–137), and `exactOutput`.

**Result — impossible choice for the pool admin:**

| Admin action | Consequence |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router; broken UX |
| **Allowlist the router** | Every user on-chain can call `router.exactInputSingle` and bypass the allowlist |

There is no configuration that simultaneously supports router-mediated swaps and enforces per-user restrictions.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to be a curated, permissioned venue — e.g., restricted to KYC'd counterparties, specific protocols, or institutional traders. Once the router is allowlisted (the only way to support standard UX), any unprivileged address can trade on the pool by routing through `MetricOmmSimpleRouter`. This exposes LP funds to toxic flow the allowlist was designed to block, breaks the core pool invariant that only approved actors may swap, and constitutes a direct, fund-impacting bypass of a configured protection hook. Severity: **High** — broken core pool functionality / allowlist guard fails open for all router-mediated swaps.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical, publicly deployed periphery entry point. Any user who discovers the pool is allowlist-gated can trivially route through the router instead of calling the pool directly. No privileged access, no special tokens, and no admin cooperation is required beyond the pool admin having allowlisted the router for legitimate users — a necessary and expected configuration step.

## Recommendation

1. **Pass the originating user through the router.** Add a `swapper` field to each router swap call and forward it to the pool via `extensionData`. The `SwapAllowlistExtension` should decode and check that field instead of (or in addition to) `sender`.
2. **Alternatively, check `sender` against the router and then require a user-level proof in `extensionData`** (e.g., a signed permit or an on-chain registry lookup keyed by the actual EOA).
3. **Document the limitation clearly** in `SwapAllowlistExtension` NatSpec: the current `sender` check is only meaningful for direct pool calls; router-mediated swaps present the router address as `sender`.

## Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)       // alice is the only allowed swapper
  admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it

Attack (bob, not allowlisted):
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)            // msg.sender to pool = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
    → check PASSES
    → bob's swap executes on the allowlisted pool

Verification:
  bob calls pool.swap(...) directly
    → pool calls _beforeSwap(sender=bob, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][bob] == false
    → reverts NotAllowedToSwap                         // direct call correctly blocked

  Conclusion: router path bypasses the per-user allowlist entirely.
```