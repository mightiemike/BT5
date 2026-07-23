Audit Report

## Title
`SwapAllowlistExtension` gates on router address instead of originating user, allowing any caller to bypass per-user swap allowlists via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap()` sets to `msg.sender` — the router address when a user routes through `MetricOmmSimpleRouter`. Any pool admin who allowlists the router (a necessary step for vetted users to trade via the standard periphery) simultaneously opens the gate to every unprivileged address, because the extension sees only the router and never the originating user.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // router address when called via router
    recipient,
    ...
    extensionData
);
```

`ExtensionCalling._beforeSwap` forwards `sender` verbatim to every configured extension via `abi.encodeCall`. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The lookup becomes `allowedSwapper[pool][router]`.

When Alice (an allowlisted user) calls `router.exactInputSingle()`, the router calls `pool.swap()` with `msg.sender = router`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][alice]`. For Alice to use the router at all, the admin must set `allowedSwapper[pool][router] = true`. Once that entry exists, Mallory (never individually allowlisted) can call `router.exactInputSingle({pool: pool, ...})` and the extension evaluates `allowedSwapper[pool][router]` → `true` → no revert. Mallory's swap executes on the curated pool.

`DepositAllowlistExtension` avoids this exact problem because `addLiquidity` carries an explicit `owner` parameter that identifies the economic actor regardless of intermediary. `swap()` has no equivalent parameter; the extension has no way to recover the originating user's address.

## Impact Explanation
Any address can bypass a curated pool's per-user swap allowlist by routing through `MetricOmmSimpleRouter` whenever the admin has allowlisted the router. Unauthorized traders gain access to a pool whose LP positions were sized and priced under the assumption that only vetted counterparties would trade. This directly exposes LP principal to adverse selection from unvetted flow, constituting a direct loss of LP assets above Sherlock thresholds.

## Likelihood Explanation
The trigger is a routine, non-malicious admin action: allowlisting the router so that vetted users can trade through the standard periphery. Any pool that (a) uses `SwapAllowlistExtension` and (b) allowlists the router is immediately vulnerable. Because the router is the canonical user-facing entry point, this configuration is the expected production setup for curated pools that want to support normal UX. No special attacker capability is required beyond calling a public router function.

## Recommendation
Add an explicit `swapper` / `user` parameter to the pool's `swap()` interface analogous to the `owner` parameter in `addLiquidity()`, and gate `SwapAllowlistExtension` on that parameter. As an interim fix, gate on `recipient` (the address that receives pool output, which the router sets to the actual user via `params.recipient`). Until the interface is extended, document that `SwapAllowlistExtension` cannot enforce per-user policies for router-mediated swaps and that allowlisting the router opens the gate to all callers.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only intended swapper.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Mallory (never allowlisted) calls `router.exactInputSingle({pool: pool, recipient: mallory, ...})`.
5. Router calls `pool.swap(mallory, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, mallory, ...)` — `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Mallory's swap executes on the curated pool, bypassing the per-user allowlist entirely.