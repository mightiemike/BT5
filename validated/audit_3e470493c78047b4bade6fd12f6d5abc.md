Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Immediate Pool Caller Instead of End-User, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swap access by checking the `sender` argument passed by the pool, which the pool sets to `msg.sender` of `pool.swap()` — the immediate caller, not the original end-user. When `MetricOmmSimpleRouter` is used, the router is the immediate caller, so the extension checks the router's address. A pool admin who allowlists the router to support the standard periphery path inadvertently grants any unprivileged user the ability to bypass the allowlist entirely.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // <-- immediate caller of pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks this value against the allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

When a user calls `router.exactInputSingle(...)`, the pool's `msg.sender` is the router. The extension evaluates `allowedSwapper[pool][router]`. If the admin has allowlisted the router (necessary for any allowlisted user to use the standard periphery path), the check passes for **any** caller of the router — including users explicitly excluded from the allowlist.

The integration test in `FullMetricExtensionTest.test_allowedSwapSucceeds` confirms this is the actual behavior: it allowlists `callers[0]` (the `TestCaller` intermediary contract), not `users[0]` (the actual human user), and the swap succeeds because the pool passes the intermediary's address as `sender`.

**Two failure modes result:**

| Scenario | Outcome |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router — core periphery path broken |
| Router **is** allowlisted | Any user bypasses the allowlist by routing through the router |

## Impact Explanation

On any pool that configures `SwapAllowlistExtension` to restrict swap access and allowlists the router to support the standard periphery path, the allowlist is completely ineffective. Any unprivileged user can execute swaps on a curated pool by routing through `MetricOmmSimpleRouter`. This is a direct admin-boundary break: the pool admin's configured access control is bypassed by an unprivileged path (the public router). Pools designed to restrict trading to KYC'd counterparties, institutional LPs, or specific strategies have their protection nullified.

## Likelihood Explanation

High. `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Pool admins who deploy allowlisted pools and want their allowlisted users to use the standard router **must** allowlist the router address — there is no other supported path. The allowlist state is readable on-chain, so any user who observes the router address in the allowlist can immediately exploit the bypass. The mistake is structurally forced by the design.

## Recommendation

The pool must forward the original end-user's address — not the router's address — as `sender` to the extension. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should accept an explicit `sender` parameter (the original `msg.sender` of the router call) and pass it to `pool.swap()` as the `sender` argument, rather than relying on the pool to use `msg.sender`.

2. **Extension-side**: `SwapAllowlistExtension` should document that `sender` must be the economic actor, and the pool interface should enforce that the `sender` argument to `swap()` is the address the pool will attribute the trade to — not necessarily `msg.sender`.

The invariant to enforce: the identity checked by the allowlist must be the same identity that the pool attributes the economic action to, regardless of which supported public entrypoint reaches the pool.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowed swapper.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(...)`. Inside the pool, `msg.sender == router`.
6. The pool calls `extension.beforeSwap(router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true`.
8. Bob's swap executes successfully — the allowlist is bypassed.

This is directly analogous to the existing `test_allowedSwapSucceeds` test pattern: replace `TestCaller` with `MetricOmmSimpleRouter` and `users[0]` with an unprivileged address.