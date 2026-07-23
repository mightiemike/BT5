Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which `MetricOmmPool.swap()` sets to `msg.sender` — the immediate caller of the pool. When `MetricOmmSimpleRouter` calls the pool, `sender` equals the router contract address, not the originating user. A pool admin who allowlists the router to enable router-based swaps inadvertently grants every user access to the curated pool, because the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap()` forwards that value verbatim to every configured extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`.

`SwapAllowlistExtension.beforeSwap` then enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Inside the extension, `msg.sender` is the pool and `sender` is whoever called the pool. When `MetricOmmSimpleRouter.exactInputSingle()` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)`, the pool's `msg.sender` is the router. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

Once a pool admin calls `setAllowedToSwap(pool, router, true)` — a natural configuration for any curated pool that also wants to support the standard periphery router — the guard condition `!allowedSwapper[pool][router]` is permanently `false` for every router-mediated call. Any user, regardless of individual allowlist status, can bypass the guard by routing through `MetricOmmSimpleRouter`.

## Impact Explanation
Any user not individually allowlisted can swap on a curated pool by calling `MetricOmmSimpleRouter` instead of the pool directly. The allowlist's purpose — restricting swaps to approved counterparties — is completely defeated. LPs who deployed capital into a curated pool expecting only approved counterparties face unrestricted swap exposure. If the pool's curation was designed to limit adverse selection, regulatory exposure, or front-running, the bypass translates to direct LP principal loss. This matches the allowed impact gate: broken core pool functionality causing loss of funds, and admin-boundary break where an unprivileged path bypasses a required hook decision.

## Likelihood Explanation
The bypass requires the pool admin to allowlist the router. This is a natural and expected configuration for any curated pool that also wants to support the standard periphery router. The admin has no obvious signal that allowlisting the router collapses the per-user allowlist — the two operations appear independent. The attacker (Bob) is an unprivileged trader who simply calls the router. No special privileges, flash loans, or complex setup are required beyond the router being allowlisted.

## Recommendation
The extension must gate on the actual end user, not the immediate pool caller. Two viable approaches:

1. Require the router to embed the originating user address in `extensionData`, and have the extension decode and check that address against the allowlist.
2. Add a dedicated `swapOnBehalf(address user, ...)` entry point to the pool that passes `user` as a distinct parameter to extensions, allowing extensions to check the economic actor rather than the intermediary.

`DepositAllowlistExtension` already demonstrates the correct pattern for deposits — it checks `owner` (the LP position recipient) rather than `sender` (the caller). The swap allowlist should apply the same principle: gate on the identity that receives the economic benefit of the swap, not the intermediary contract that relays the call.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps.
3. Pool admin calls `setAllowedToSwap(pool, alice, true)` to allowlist alice for direct swaps. Bob is not allowlisted.
4. Bob calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(recipient=bob, ...)` — `msg.sender` at the pool is the router.
6. Pool calls `_beforeSwap(router, bob, ...)` — extension receives `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes successfully despite not being individually allowlisted.