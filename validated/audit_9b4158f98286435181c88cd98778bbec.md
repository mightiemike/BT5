Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any User to Bypass the Per-User Swap Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` = `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router address (required for any router-mediated swap to succeed), every user — including those never individually approved — can bypass the per-user gate by calling the public router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the originating user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
```

The pool therefore sees `msg.sender = router`. The extension checks `allowedSwapper[pool][router]`. The actual end-user address is never visible to the extension.

This creates an irresolvable dilemma: if the admin does not allowlist the router, all router-mediated swaps revert even for individually approved users. If the admin allowlists the router, every user — approved or not — can swap through the router, bypassing the per-user gate entirely.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates the `owner` argument (the economic actor), not `sender` (the operator/payer):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The deposit path separates `sender` (operator/payer) from `owner` (position owner), enabling the operator pattern. No equivalent separation exists on the swap path — `sender` is the only actor forwarded to extensions, and it collapses to the router address when the router is used.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC-approved or institutional counterparties loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` through the public router and trade against the pool's LP positions. This is a direct admin-boundary break: an access-control policy set by the pool admin is bypassed by an unprivileged path through a supported periphery contract, with direct exposure of LP assets to unapproved counterparties.

## Likelihood Explanation
The router is the primary user-facing entry point documented and deployed by the protocol. A pool admin who wants approved users to be able to use the router must allowlist the router address — this is a natural, expected operational step. Once taken, the bypass is unconditional and requires no further attacker action beyond calling the public router. The precondition (router allowlisted) is the normal operational state for any pool that intends to support router-mediated swaps.

## Recommendation
Pass the originating user through the swap path so the extension can gate the correct actor. Two options:

1. **Add an `originator` field to swap parameters.** The router sets `originator = msg.sender` before calling `pool.swap()`; the pool forwards it to extensions alongside `sender`. Extensions check `allowedSwapper[pool][originator]`.

2. **Mirror the deposit pattern.** Require callers to supply a `swapper` address (analogous to `owner` in `addLiquidity`). The pool gates on that address, and the router passes `msg.sender` as `swapper`. This keeps the extension check independent of the intermediate caller.

Either fix ensures the allowlist gates the economically relevant actor regardless of which supported periphery path reaches the pool.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, address(router), true)` so that approved users can trade through the router.
3. Non-approved user `attacker` (not in `allowedSwapper[pool]`) calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
5. Extension checks `allowedSwapper[pool][router] == true` → passes.
6. Attacker's swap executes against LP positions despite never being individually approved.