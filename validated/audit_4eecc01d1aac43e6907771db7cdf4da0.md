Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist, but `MetricOmmPool.swap` always binds `sender` to `msg.sender` — the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. A pool admin who allowlists the router to support periphery-mediated swaps inadvertently grants every user — including non-allowlisted ones — the ability to bypass the allowlist entirely.

## Finding Description

`MetricOmmPool.swap` unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // always the direct caller of pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is used, it calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., "", params.extensionData);
```

This makes the router the `msg.sender` at the pool level, so `sender = router`. For any router-mediated swap to succeed on an allowlisted pool, the pool admin must add `allowedSwapper[pool][router] = true`. Once set, the check passes for **every** caller of the router, regardless of whether that caller is individually allowlisted.

Additionally, `SwapAllowlistExtension.beforeSwap` drops the `onlyPool` modifier that `BaseMetricExtension.beforeSwap` declares (L81-88), removing the defense-in-depth layer that prevents direct external calls to the extension entry point.

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to KYC'd or whitelisted counterparties loses that guarantee the moment the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and execute swaps on the restricted pool. The allowlist policy is completely nullified for router-mediated paths, which is the primary user-facing entry point for the protocol. This constitutes a broken core pool functionality (access control invariant) that enables unauthorized swap execution on pools designed to restrict participation.

**Severity: Medium** — direct policy bypass on curated pools; the allowlist invariant is broken for all router-mediated swaps.

## Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical periphery swap entry point. Any pool admin who wants end users to swap through the router (the expected UX) must allowlist the router. This is a natural, expected configuration step. The bypass is therefore reachable on any allowlisted pool that also supports router access, which is the common case. No special privileges or unusual conditions are required — any unprivileged user can exploit this by simply calling the router.

## Recommendation

The extension must gate the **economic actor** (the end user), not the intermediary (the router). Two viable approaches:

1. **Pass the originating user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. The pool already forwards `extensionData` unchanged to every extension hook.

2. **Check both router and originating user**: Require the router to be a trusted forwarder that appends the original user address, and have the extension verify the appended address against the allowlist when `sender` is a known router.

The base class `onlyPool` modifier should also be restored in `SwapAllowlistExtension.beforeSwap` to prevent direct external calls to the extension.

## Proof of Concept

**Setup**:
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin allowlists Alice (a KYC'd user): `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router to support periphery access: `setAllowedToSwap(pool, router, true)`.

**Attack**:
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — `msg.sender = router`.
6. Pool calls `_beforeSwap(msg.sender=router, ...)` → extension receives `sender=router`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes successfully on the restricted pool, bypassing the allowlist entirely.

**Control (direct pool call)**:
- Bob calls `pool.swap(...)` directly → `sender = bob` → `allowedSwapper[pool][bob]` = false → reverts with `NotAllowedToSwap`. ✓

The bypass is exclusive to the router path, confirming the wrong-actor binding as the root cause.