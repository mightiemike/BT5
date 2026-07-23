Audit Report

## Title
Swap Allowlist Bypass via Router — Any User Can Bypass `SwapAllowlistExtension` by Routing Through `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, the router is always the direct caller of `pool.swap()`, so the extension receives `sender = router_address`. Any pool admin who allowlists the router to support router-mediated swaps inadvertently grants all users access, completely defeating the per-user allowlist.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` is the pool and `sender` is the first argument forwarded by the pool.

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` at line 231, passing the direct caller of `pool.swap()` as `sender`. `ExtensionCalling._beforeSwap` encodes this value as the first argument to the extension call.

In every router path, the router is the direct caller of `pool.swap()`:
- `exactInputSingle`: router calls `pool.swap()` directly (line 72–80).
- `exactInput`: router calls `pool.swap()` for every hop in the loop (line 104–112).
- `exactOutputSingle`: router calls `pool.swap()` directly (line 136–137).
- `exactOutput`: router calls `pool.swap()` for the last hop (line 165–181); intermediate hops are called from within `_exactOutputIterateCallback`, which executes in the router's context, so `msg.sender` of each intermediate pool is still the router (line 220–228).

In all cases, the extension receives `sender = router_address`. If the admin allowlists the router (`allowedSwapper[pool][router] = true`), the check passes for any user who routes through the router, regardless of whether that user is individually allowlisted.

The admin faces an irresolvable dilemma: not allowlisting the router blocks all router-mediated swaps for allowlisted users (DoS on the primary UX path); allowlisting the router opens the pool to all users.

## Impact Explanation
The `SwapAllowlistExtension` is a core pool access-control mechanism for curated/restricted pools. When the router is allowlisted, the per-user allowlist is completely bypassed for any user who calls through `MetricOmmSimpleRouter`. Disallowed users can trade on restricted pools, constituting a broken core pool functionality with direct access-control failure. This matches the allowed impact: "Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows" and "Admin-boundary break: pool admin exceeds caps, bypasses timelocks, or factory/oracle role checks are bypassed by an unprivileged path."

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported swap entrypoint for end users. Any pool admin who deploys a curated pool and wants users to use the router (the normal UX path) will allowlist the router, triggering the bypass. No special privileges are required — any user can call the router. The trigger is deterministic and repeatable.

## Recommendation
The extension must gate the **original user**, not the intermediary router. Viable approaches:
1. **Pass original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it, with a trust assumption that the router is the only allowed intermediary.
2. **Trusted router registry**: The extension maintains a registry of trusted routers; when `sender` is a trusted router, it reads the original user from `extensionData` and checks that address instead.
3. **Enforce direct-call-only**: Document and enforce at the factory/config validation layer that `SwapAllowlistExtension` is incompatible with router-mediated swaps.

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin allowlists user1:
       swapExt.setAllowedToSwap(pool, user1, true)
3. Admin allowlists the router to support router-mediated swaps:
       swapExt.setAllowedToSwap(pool, router, true)
4. user2 (NOT allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap() → msg.sender of pool = router.
6. Pool calls _beforeSwap(router, ...) → extension receives sender = router.
7. Extension checks allowedSwapper[pool][router] → true → passes.
8. user2 successfully swaps on the curated pool, bypassing the allowlist.
```