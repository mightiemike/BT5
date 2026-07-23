Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of actual swapper, breaking per-user allowlist enforcement - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` — the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual user. This breaks the allowlist in both directions: allowlisted users cannot swap through the router, and if the router is itself allowlisted, any non-allowlisted user can bypass the restriction entirely.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender`:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)` at line 231. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `IMetricOmmPoolActions(params.pool).swap(...)` at line 72–80. Inside the pool, `msg.sender` is the router contract address, not the originating user.

**Step 2 — Extension receives the router address as `sender`:**

`ExtensionCalling._beforeSwap` forwards `sender` verbatim to every configured extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))` at lines 162–175.

**Step 3 — Extension checks the wrong address:**

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` at line 37, where `msg.sender` is the pool (correct) and `sender` is the router (wrong). The check is therefore `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Step 4 — Contrast with `DepositAllowlistExtension` (correct design):**

`DepositAllowlistExtension.beforeAddLiquidity` deliberately ignores the `sender` parameter (first argument is unnamed/discarded) and instead checks `allowedDepositor[msg.sender][owner]` at line 38, where `owner` is the actual depositor passed explicitly by the pool. This confirms the design intent is to check the real actor, not the intermediary — and that `SwapAllowlistExtension` deviates from this pattern.

**Exploit paths:**

- *Router not allowlisted*: Allowlisted users (e.g., Alice) cannot swap through the router because `allowedSwapper[pool][router]` is false. The primary user-facing interface is broken for all allowlisted users.
- *Router allowlisted*: Any non-allowlisted user (e.g., Bob) can bypass the per-user restriction by calling `MetricOmmSimpleRouter.exactInputSingle(...)`. The extension checks `allowedSwapper[pool][router] == true` and permits the swap regardless of Bob's allowlist status.

No existing guard prevents either path. The pool has no mechanism to pass the originating user identity to extensions; `extensionData` is caller-controlled and not authenticated.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers) provides no effective restriction when users route through `MetricOmmSimpleRouter`. If the router is allowlisted, any address can trade in the restricted pool by calling the router — an admin-boundary break where an unprivileged, non-allowlisted user executes swaps the pool admin explicitly prohibited. If the router is not allowlisted, the allowlist breaks core swap functionality for all allowlisted users through the primary interface.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. A pool admin who wants to allow router-based trading while restricting individual users would naturally allowlist the router, unknowingly enabling the bypass. Alternatively, any allowlisted user attempting to use the router will discover the breakage immediately. No special privileges, flash loans, or unusual token behavior are required.

## Recommendation

`SwapAllowlistExtension.beforeSwap` should check the actual originating user, not the intermediary caller. Two options:

1. **Preferred**: Add an explicit `swapper` field to `extensionData` that the router populates with `msg.sender` before calling the pool. The extension decodes it and verifies `msg.sender` (the pool) is a trusted factory-registered pool before accepting the embedded identity.
2. **Alternative**: Mirror the `DepositAllowlistExtension` pattern — have the pool pass the originating user as a dedicated parameter (analogous to `owner` in `addLiquidity`) rather than relying on `msg.sender` propagation through intermediaries.

## Proof of Concept

1. Pool admin deploys a pool and attaches `SwapAllowlistExtension`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)` (to allow router-based trading).
3. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. Router calls `pool.swap(recipient, ...)` — inside the pool, `msg.sender = router`.
5. `_beforeSwap(router, recipient, ...)` is called; extension evaluates `allowedSwapper[pool][router] == true`.
6. Bob's swap succeeds despite not being on the allowlist.

Alternatively, without the router allowlisted: Alice calls `MetricOmmSimpleRouter.exactInputSingle(...)` — the extension checks `allowedSwapper[pool][router] == false` and reverts with `NotAllowedToSwap`, blocking Alice from using the primary interface despite being explicitly allowlisted.

A Foundry integration test can reproduce both paths by deploying the pool with `SwapAllowlistExtension`, configuring the allowlist, and calling through the router vs. directly.