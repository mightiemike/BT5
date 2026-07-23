Audit Report

## Title
Swap Allowlist Checks Router Address Instead of Actual User, Enabling Complete Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating EOA. If the pool admin allowlists the router address to enable router-mediated swaps, every unprivileged user can bypass the per-user allowlist by calling through the public router. The intended curation invariant — only allowlisted addresses may swap — is completely broken.

## Finding Description

**Exact call chain:**

1. Unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle()` (`msg.sender = user`).
2. Router stores `msg.sender` as payer in transient storage, then calls `IMetricOmmPoolActions(params.pool).swap(...)` — at this point `msg.sender` seen by the pool is the **router address**.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` where `msg.sender = router`.
4. `ExtensionCalling._beforeSwap()` encodes `sender = router_address` and dispatches to `SwapAllowlistExtension.beforeSwap`.
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]`.

The extension never sees the originating EOA. The pool admin has two bad options:
- **Allowlist the router**: every user (including non-allowlisted ones) can bypass the gate by calling through the public router.
- **Do not allowlist the router**: even allowlisted users cannot use the router; only direct `pool.swap()` calls work.

**Root cause:** `MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as `sender` to the extension hook. The router is an intermediary, so the extension always sees the router, never the originating user.

**Existing guards are insufficient:** `BaseMetricExtension.onlyPool` only verifies the caller is a registered pool; it does not recover the original EOA. There is no mechanism in the extension interface to receive the true originator.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses is fully bypassed: any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) on the allowlisted pool and executes swaps that the pool admin intended to block. This is a direct curation failure on curated pools and constitutes a broken core pool functionality / admin-boundary break by an unprivileged path. Severity: **High**.

## Likelihood Explanation
The router is the primary public entry point for swaps. Any user can call it permissionlessly. No special setup is required beyond the pool admin having allowlisted the router (a natural configuration to enable router-mediated swaps for legitimate users). The bypass is repeatable every block.

## Recommendation
The extension must receive and check the true originating user, not the immediate pool caller. Two approaches:

1. **Pass originator through the pool**: Add an `originator` field to the swap interface that the router populates with `msg.sender` before calling the pool, and thread it through `_beforeSwap` to the extension.
2. **Check the router's stored payer**: The extension could call back to the router to retrieve the stored payer from transient storage, but this creates coupling between extension and periphery.
3. **Allowlist at the router level**: The router enforces its own per-user allowlist before calling the pool, and the pool allowlists only the router. This moves curation to the periphery layer, which must then be trusted.

The cleanest fix is option 1: extend the `beforeSwap` hook signature with an `originator` address that the pool populates from a caller-supplied parameter, allowing the extension to gate the true economic actor.

## Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; router is allowlisted, attacker is not
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// attacker is NOT in allowedSwapper

// Attacker bypasses allowlist via router
vm.prank(attacker); // attacker is not allowlisted
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Extension checks allowedSwapper[pool][router] == true → passes
// Attacker successfully swaps on a pool they are not allowlisted for
```