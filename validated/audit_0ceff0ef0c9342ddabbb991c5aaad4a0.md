Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender`. When `MetricOmmSimpleRouter` is used, the pool's `msg.sender` is the router contract, not the originating user. The original user's address is stored only in transient callback context for payment and is never forwarded to the pool or any extension. The allowlist therefore evaluates the router's permission, not the end user's, making the restriction either fully bypassable or permanently broken for router-mediated paths.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender`:**
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 231. When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract address.

**Step 2 — Extension receives the router address as `sender`:**
`ExtensionCalling._beforeSwap` encodes and forwards `sender` unchanged to every configured extension via `_callExtensionsInOrder` (lines 160–176). No transformation or originator lookup occurs.

**Step 3 — Extension checks the wrong address:**
`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` at line 37, where `msg.sender` is the pool and `sender` is the router. The end user's address is never consulted.

**Step 4 — Router never forwards the originating user:**
`MetricOmmSimpleRouter.exactInputSingle` stores the originating user only in transient callback context via `_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)` at line 71, solely for payment settlement. The call to `pool.swap(...)` at lines 72–80 passes no originator field. The same pattern holds for `exactInput` (line 103), `exactOutputSingle` (line 135), `exactOutput` (line 165), and recursive hops in `_exactOutputIterateCallback` (line 220).

**Existing guards are insufficient:** There is no mechanism in the pool interface, `ExtensionCalling`, or `SwapAllowlistExtension` to recover the originating user's address from the router call. The `extensionData` bytes field is user-supplied and unverified, so it cannot be trusted as an originator signal without a protocol-level convention that does not exist.

## Impact Explanation

Two mutually exclusive fund-impacting failure modes arise:

**Mode A — Allowlist bypass (High severity):** A pool admin allowlists the router so approved users can reach the pool via the standard periphery path. Because the extension checks the router address, every caller of the router is implicitly allowlisted. Any unprivileged address can bypass the curated-pool restriction and execute swaps, draining liquidity at oracle prices the pool admin intended to reserve for specific counterparties.

**Mode B — Broken core swap functionality (Medium severity):** A pool admin allowlists only specific EOAs and does not allowlist the router. Every allowlisted user who attempts a router-mediated swap receives `NotAllowedToSwap`, even though they are explicitly permitted. The router path is permanently broken for all allowlisted users on that pool.

Both modes break the invariant the extension was configured to enforce and constitute either direct loss of funds (Mode A) or broken core functionality (Mode B).

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Pool admins who configure `SwapAllowlistExtension` will naturally want their approved users to use the router. The moment they allowlist the router to restore router functionality (Mode A), the allowlist is fully bypassed with no further privileged action required. Any unprivileged address can then exploit it by calling any router entry point. The trigger is the pool admin's own expected configuration step.

## Recommendation

The pool's `swap` interface passes only `msg.sender` as `sender`, with no originator field. Two viable fixes exist:

1. **`extensionData` encoding:** The router encodes the originating user's address in `extensionData` using a well-known prefix. `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `msg.sender` (the pool's caller, i.e., the router) is a recognized router address. This requires a trusted router registry or a factory-level convention.

2. **Explicit `originator` parameter:** Add an `originator` field to the pool's `swap` interface that the router populates with its own `msg.sender`. The extension checks `originator` instead of `sender`. This is a breaking interface change but is the cleanest solution.

Either approach ensures the extension always gates the economically relevant actor rather than the intermediary.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary to allow router-mediated swaps for approved users.
3. Unprivileged `attacker` (not in `allowedSwapper`) calls `MetricOmmSimpleRouter.exactInputSingle` targeting the pool.
4. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` is the router.
5. Pool calls `_beforeSwap(router, ...)` — `sender = router`.
6. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
7. Swap executes. `attacker` receives output tokens. Allowlist restriction is completely bypassed.

The same path works for `exactInput`, `exactOutputSingle`, `exactOutput`, and every intermediate hop in `_exactOutputIterateCallback`.