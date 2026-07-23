Now I have all the information needed to evaluate the claim. Let me trace the exact actor-binding path:

1. `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` (line 231)
2. `ExtensionCalling._beforeSwap` forwards that `sender` to the extension (line 165)
3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`
4. When `MetricOmmSimpleRouter.exactInputSingle` is used, the router calls `pool.swap()` — so `sender` received by the extension is the **router address**, not the original user

This is a confirmed actor-binding mismatch.

Audit Report

## Title
SwapAllowlistExtension checks router address instead of actual user, enabling allowlist bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the router is allowlisted (required for any user to swap via the router on a curated pool), every user — including non-allowlisted ones — can bypass the per-user gate by routing through the router.

## Finding Description
**Call path:**
1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)` — `msg.sender = user`
2. Router calls `IMetricOmmPoolActions(params.pool).swap(...)` — pool sees `msg.sender = router`
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` → `_beforeSwap(router, ...)` (`MetricOmmPool.sol` line 231)
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to the extension (`ExtensionCalling.sol` lines 162–165)
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]` (`SwapAllowlistExtension.sol` line 37)

**Root cause:** The pool unconditionally passes its own `msg.sender` as `sender` to extensions. There is no mechanism to propagate the original EOA through the router. The extension has no access to the true initiating address.

**Why existing guards fail:** The `onlyPool` guard in `BaseMetricExtension` only ensures the extension is called by a registered pool — it does not validate the identity of the economic actor. The allowlist mapping is keyed by `(pool, sender)` where `sender` is the immediate pool caller, not the end user.

**Bypass scenario:** A pool admin configures a curated pool with `SwapAllowlistExtension` and must allowlist the router address to permit any user to swap via the router. Once `allowedSwapper[pool][router] = true`, every user — regardless of their own allowlist status — can call `MetricOmmSimpleRouter.exactInputSingle` and pass the gate, because the extension sees `sender = router` (allowlisted) rather than the user's address (not allowlisted).

## Impact Explanation
This is a direct allowlist bypass on curated pools. Any user can trade on a pool that was designed to restrict swaps to a specific set of addresses (e.g., KYC-compliant counterparties, whitelisted market makers). The pool's curation policy is entirely nullified for router-mediated swaps, which is the primary supported public entrypoint. This constitutes broken core pool functionality and a policy bypass with direct fund-flow impact (unauthorized parties execute swaps and receive output tokens).

## Likelihood Explanation
The attack requires only that the router is allowlisted on the target pool — a condition that must hold for any allowlisted user to use the router at all. Any unprivileged user can then call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router targeting the curated pool. No special privileges, flash loans, or multi-step setup are needed. The attack is repeatable every block.

## Recommendation
The extension must gate on the true originating user, not the immediate pool caller. Options:
1. **Pass the original sender explicitly:** Extend the swap interface so the router forwards the originating `msg.sender` as a verified field in `extensionData`, and have `SwapAllowlistExtension` decode and verify it (requires a trusted router registry or signature).
2. **Check `tx.origin` as a fallback:** Use `tx.origin` when `msg.sender` (the pool's caller) is a known router. This is fragile but closes the immediate bypass.
3. **Preferred — router-level allowlist enforcement:** Require the router to check the allowlist before calling the pool, and have the extension trust only direct pool callers (non-contract addresses). This requires the extension to distinguish EOA callers from contract callers.
4. **Simplest fix:** Document that the router must never be allowlisted and that allowlisted users must call the pool directly. This is a usability constraint but closes the bypass without code changes.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted as a swapper.
// Pool admin also allowlists the router so alice can use it.
swapExtension.setAllowedToSwap(pool, alice, true);
swapExtension.setAllowedToSwap(pool, address(router), true); // required for alice to use router

// Attack: bob (not allowlisted) calls the router directly
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Extension sees sender = address(router) → allowedSwapper[pool][router] = true → passes
// Bob successfully swaps on a pool he should be blocked from
```