Audit Report

## Title
Swap Allowlist Bypass via Router — Any User Can Bypass `SwapAllowlistExtension` by Routing Through `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `pool.swap()`. When any swap is routed through `MetricOmmSimpleRouter`, the router is always the direct caller of `pool.swap()`, so `sender` resolves to the router address rather than the originating user. Any pool admin who allowlists the router to support router-mediated swaps inadvertently opens the pool to all users, completely defeating the per-user allowlist.

## Finding Description
**Root cause in `SwapAllowlistExtension.beforeSwap`:**

The check at `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol` L37 is:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` is the pool (the extension caller) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of `pool.swap()`.

**Pool unconditionally forwards `msg.sender` as `sender`:**

`MetricOmmPool.swap()` at L230–240 calls `_beforeSwap(msg.sender, ...)`. This means whoever calls `pool.swap()` becomes the `sender` seen by the extension. There is no mechanism to pass the original end-user through this argument.

**Router is always the direct caller of `pool.swap()`:**

- `exactInputSingle` (L71–80): router calls `pool.swap()` directly.
- `exactInput` (L103–112): router calls `pool.swap()` for every hop.
- `exactOutputSingle` (L136–137): router calls `pool.swap()` directly.
- `exactOutput` (L165–181): router calls `pool.swap()` for the last hop.
- `_exactOutputIterateCallback` (L220–228): intermediate hops are called from within the callback, which executes in the router's context, so `msg.sender` of each intermediate `pool.swap()` is still the router.

In every router path, the extension receives `sender = router_address`.

**The dilemma for pool admins:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router (DoS on the primary UX path) |
| Allowlist the router | **All** users bypass the per-user allowlist via the router |

Once `allowedSwapper[pool][router] = true`, any non-allowlisted user calls `router.exactInputSingle()` and the extension passes because it sees the router address, not the user.

## Impact Explanation
The `SwapAllowlistExtension` is a core pool access-control mechanism for curated/restricted pools. When the router is allowlisted (the only way to support the primary swap UX), the allowlist is completely defeated — any unprivileged user can trade on a restricted pool. This constitutes broken core pool functionality with direct curation failure: disallowed users gain unauthorized access to private liquidity pools, representing a regulatory/compliance failure and a broken access-control invariant. The wrong value checked is `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][original_user]`.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported swap entrypoint for end users. Any pool admin who deploys a curated pool and wants users to use the router (the normal UX path) will allowlist the router, triggering the bypass. No special privileges are required — any user can call the router. The trigger condition (router allowlisted) is the expected operational state for any pool that supports router-mediated swaps.

## Recommendation
The extension must gate the **original user**, not the intermediary router. Viable approaches:

1. **Pass original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. Requires a trust assumption that the router is the only allowed intermediary.
2. **Trusted router registry**: The extension maintains a registry of trusted routers; when `sender` is a trusted router, it reads the original user from `extensionData`.
3. **Document incompatibility**: Enforce at the factory/config validation layer that `SwapAllowlistExtension` is incompatible with router-mediated swaps.

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

Root cause: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol` L37 checks `sender` (the direct pool caller) rather than the original user. The pool unconditionally forwards `msg.sender` as `sender` at `metric-core/contracts/MetricOmmPool.sol` L231, and the router is always the direct caller at `metric-periphery/contracts/MetricOmmSimpleRouter.sol` L72–80, L104–112, L136–137, L220–228.