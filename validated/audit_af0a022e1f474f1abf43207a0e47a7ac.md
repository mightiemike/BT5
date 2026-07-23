Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates on Router Address Instead of Actual Swapper, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool populates with `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `sender = router address`. The allowlist check becomes `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. Since the router must be allowlisted for any legitimate user to use it, every unprivileged user can bypass the curated-pool gate by routing through the router.

## Finding Description

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, passing its own `msg.sender` as the `sender` argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this `sender` unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The router is `msg.sender` from the pool's perspective, so `sender = router_address` reaches the extension. The check evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][real_user]`. The same structural flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

Additionally, `BaseMetricExtension.beforeSwap` declares `onlyPool` on the virtual stub, but `SwapAllowlistExtension.beforeSwap` overrides it without re-applying the modifier — Solidity does not inherit modifiers through overrides — silently dropping the defense-in-depth guard.

## Impact Explanation

This is a direct admin-boundary break. A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to specific addresses (KYC-verified counterparties, DAO members, whitelisted market makers). The admin is forced into one of two broken states:

1. **Router allowlisted** (`allowedSwapper[pool][router] = true`): Every unprivileged user can call `router.exactInputSingle(pool, ...)` and swap on the curated pool, completely defeating the allowlist. The pool's curation policy is nullified for any user who knows the router address.
2. **Router not allowlisted**: Legitimate allowlisted users cannot use the router at all; they must call `pool.swap()` directly. The supported periphery path is broken for the pool.

In case (1), unprivileged actors reach a pool action the pool admin explicitly restricted, which can translate to unauthorized extraction of LP value from a private pool with favorable oracle pricing, regulatory non-compliance, or loss of the curation invariant that LPs deposited under.

## Likelihood Explanation

- The router is the canonical swap entrypoint for end users; any pool that wants to be usable via the router must allowlist it.
- The bug is triggered by any ordinary `exactInputSingle` or `exactInput` call — no special setup, flash loan, or privileged role required.
- `SwapAllowlistExtension` is a production periphery contract, not a mock or test artifact.
- The wrong-actor binding is structural: it cannot be worked around by the pool admin without abandoning the router entirely.

## Recommendation

The extension must receive the true end-user identity. The cleanest fix is to redesign the `beforeSwap` callback to carry a `payer` or `originator` field populated by the pool from its transient callback context (already stored via `_setNextCallbackContext` in the router), so the extension always sees the address that will actually fund the swap. Alternatively, the router can encode `msg.sender` into `extensionData` and the extension can decode and verify it against a trusted router registry. Additionally, `SwapAllowlistExtension.beforeSwap` should re-apply the `onlyPool` modifier to match the base contract's intent.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (BEFORE_SWAP_ORDER = extension 1)
  - allowedSwapper[pool][alice] = true   // alice is the intended gated user
  - allowedSwapper[pool][router] = true  // admin must do this for alice to use the router
  - bob is NOT in allowedSwapper

Attack:
  1. bob calls router.exactInputSingle({pool: pool, ..., recipient: bob})
  2. router calls pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
     → pool's msg.sender = router
  3. pool calls _beforeSwap(sender=router, recipient=bob, ...)
  4. extension checks: allowedSwapper[pool][router] == true  ✓
  5. swap executes — bob swaps on the curated pool without being allowlisted

Result:
  bob receives output tokens from a pool restricted to alice only.
  The allowlist is fully bypassed for any user who routes through the router.
```

Foundry test plan: deploy `SwapAllowlistExtension`, configure a pool with `allowedSwapper[pool][router] = true` and `allowedSwapper[pool][bob] = false`, call `router.exactInputSingle` as `bob`, and assert the swap succeeds rather than reverting with `NotAllowedToSwap`.