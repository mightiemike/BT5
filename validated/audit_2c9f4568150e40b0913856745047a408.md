Audit Report

## Title
`SwapAllowlistExtension.beforeSwap()` Checks Router Address Instead of End User, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` gates swaps per pool by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. Any pool admin who allowlists the router to enable router-based trading for legitimate users simultaneously grants every user on the internet the ability to bypass the allowlist by routing through the router.

## Finding Description
In `SwapAllowlistExtension.beforeSwap()` (L37), the guard is:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender])
```
Here `msg.sender` is the pool (correct key) and `sender` is the first argument passed by the pool — which is `msg.sender` of `pool.swap()` (see `MetricOmmPool.sol` L230-231: `_beforeSwap(msg.sender, ...)`).

In `MetricOmmSimpleRouter.exactInputSingle()` (L72-80), the router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly. Therefore `msg.sender` inside `pool.swap()` is `address(router)`, not the end user. The extension receives `sender = address(router)` and evaluates `allowedSwapper[pool][router]`.

For any allowlisted user to trade via the router, the admin must call `setAllowedToSwap(pool, router, true)`. Once set, `allowedSwapper[pool][router]` is `true` for every call arriving through the router, regardless of who the actual caller of `exactInputSingle` is. The same wrong-actor binding applies to `exactInput` (L104-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181) — all router entry points call `pool.swap()` with the router as `msg.sender`.

Existing guards are insufficient: there is no secondary check on the originating user, no `extensionData` decoding of the real caller, and no mechanism in the pool or extension to distinguish the immediate caller from the economic actor.

## Impact Explanation
A curated pool using `SwapAllowlistExtension` to restrict trading (e.g., to KYC'd users, institutional counterparties, or specific protocol addresses) is fully bypassed. Any unprivileged user can trade against the pool's liquidity by routing through `MetricOmmSimpleRouter`. LP funds are exposed to counterparties the pool admin explicitly excluded, and the pool's risk model (which may depend on counterparty identity) is broken. This is a direct admin-boundary break: an unprivileged path circumvents the pool admin's access control restriction, constituting broken core pool functionality and loss of the curation guarantee for LP assets.

## Likelihood Explanation
The router is the canonical, documented periphery entry point for swaps. Any pool admin who wants allowlisted users to trade via the router must add the router to the allowlist — this is the expected operational pattern, not a misconfiguration. The bypass is therefore triggered by normal, correct admin configuration. Any user who discovers the router is allowlisted can exploit it immediately with no special privileges, no capital requirements beyond the swap itself, and no time constraints. The condition is repeatable on every swap.

## Recommendation
The extension must check the end user's identity, not the intermediary's. The preferred fix is to have `MetricOmmSimpleRouter` encode `msg.sender` (the originating user) into `extensionData` on every `pool.swap()` call, and have `SwapAllowlistExtension.beforeSwap()` decode and check that address when `sender` is a known router. Alternatively, redesign the hook signature so the pool passes the originating user separately from the immediate caller, or require that the allowlist check fall back to `recipient` when `sender` is a registered router. The pool admin should allowlist users, not the router.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Admin allowlists the router so Alice can trade via the router: `setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(params.recipient, ...)` — `msg.sender` inside `pool.swap()` is `address(router)`.
6. The pool calls `extension.beforeSwap(address(router), ...)`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully trades on a pool he was explicitly excluded from.

Foundry test: deploy pool with extension, allowlist a test router address, call `pool.swap()` from the router with an unallowlisted EOA as the economic actor, assert no revert.