Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual end-user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which resolves to `msg.sender` of `MetricOmmPool.swap` — the router contract, not the end-user. When a pool admin allowlists the router to enable router-based swaps for KYC-approved users, every non-allowlisted address can bypass the per-user restriction by calling through `MetricOmmSimpleRouter`. The per-user allowlist is completely nullified for all router-mediated swaps, breaking the core curation guarantee the extension is meant to enforce.

## Finding Description
The call chain is as follows:

**Step 1 — Extension check** (`SwapAllowlistExtension.sol` L37):
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
`msg.sender` here is the pool (correct for the pool-keyed mapping). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`.

**Step 2 — Pool passes `msg.sender` as `sender`** (`MetricOmmPool.sol` L230–231):
```solidity
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    ...
```
When the router calls `pool.swap()`, `msg.sender` inside the pool is the router address.

**Step 3 — Router calls pool directly** (`MetricOmmSimpleRouter.sol` L72–80):
```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```
The router is the direct caller of `pool.swap()`, so `sender = router` reaches the extension.

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. If the admin has allowlisted the router, the check passes for any caller of the router.

**Contrast with `DepositAllowlistExtension`** (`DepositAllowlistExtension.sol` L38):
```solidity
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```
`addLiquidity` carries an explicit `owner` parameter representing the actual position owner, so the deposit guard correctly checks the intended actor. The swap path has no equivalent "on-behalf-of" field, so the guard silently degrades to a caller-contract check.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router — a natural operational step to let KYC-approved users trade via the standard periphery — inadvertently opens the pool to every user. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutput`, or `exactOutputSingle` and the extension will pass because `allowedSwapper[pool][router] == true`. This breaks the core access-control invariant the extension is designed to enforce: that only explicitly approved addresses may swap on a curated pool. This constitutes broken core pool functionality with direct impact on the pool admin's intended access control boundary.

## Likelihood Explanation
Medium. The bypass requires the pool admin to have allowlisted the router. This is a realistic and expected operational step: without it, even allowlisted users cannot trade through the standard periphery. The design trap is subtle because `DepositAllowlistExtension` works correctly for the analogous liquidity path, giving admins no reason to suspect the swap path behaves differently. The bypass is repeatable by any unprivileged address once the router is allowlisted.

## Recommendation
Introduce an explicit "on-behalf-of" address in the swap path, analogous to the `owner` parameter in `addLiquidity`, and forward it through `beforeSwap`. The extension should check that address rather than `sender`. Alternatively, document clearly that `SwapAllowlistExtension` gates caller contracts (not end-users) and that allowlisting the router opens the pool to all users — and provide a separate per-user guard that decodes the actual user from `extensionData` (e.g., a signed permit or ABI-encoded caller address verified against a signature).

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin allowlists Alice (KYC-approved): `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router so Alice can use it: `setAllowedToSwap(pool, router, true)`.
4. Bob (non-KYC'd) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — pool's `msg.sender` = router.
6. Pool calls `_beforeSwap(router, ...)` → extension receives `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → no revert.
8. Bob's swap executes on the curated pool, bypassing the per-user allowlist entirely.

Foundry test outline:
```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. setAllowedToSwap(pool, alice, true)
// 3. setAllowedToSwap(pool, address(router), true)
// 4. vm.prank(bob); router.exactInputSingle(...)
// 5. Assert swap succeeds (no NotAllowedToSwap revert)
```