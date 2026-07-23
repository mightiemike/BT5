Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Complete Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which resolves to `msg.sender` of the pool's `swap()` call — the router address when swaps are routed through `MetricOmmSimpleRouter`. A pool admin who allowlists the router (the only way to enable router-based swaps on a curated pool) inadvertently grants every user the ability to bypass the allowlist, since the check becomes `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. This completely breaks the access-control invariant of any pool configured with this extension.

## Finding Description
**Root cause:** `SwapAllowlistExtension.beforeSwap()` evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension caller) and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as `sender` to `_beforeSwap()`:

```solidity
_beforeSwap(
    msg.sender,   // direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` encodes that value verbatim into the extension call. When `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point) calls `pool.swap()`, the router is `msg.sender` of that call. The original user's address is stored only in transient callback context via `_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)` and is never forwarded to the pool as `sender`. The extension therefore receives `sender = router_address`, and the check becomes `allowedSwapper[pool][router]`. If the router is allowlisted (required for any router-based swap), every user — including non-allowlisted ones — passes the check. The same structural problem exists for `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` with `msg.sender = router`.

## Impact Explanation
A curated pool configured with `SwapAllowlistExtension` (e.g., for KYC, institutional, or compliance gating) loses its access-control guarantee the moment the router is allowlisted. Any non-allowlisted address can execute swaps at oracle-derived prices on the restricted pool by calling the router instead of the pool directly. This is a complete bypass of the configured guard with direct policy-level consequences: the pool's curation invariant is broken and cannot be restored without blocking all router-based swaps. This constitutes a broken core pool functionality / admin-boundary break where an unprivileged trader bypasses a pool admin's configured access control.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard periphery swap interface; end users are expected to use it. A pool admin who deploys a curated pool and wants to support the router will naturally allowlist the router address — this is the only way to make router swaps work. The bypass requires no special privileges, no malicious setup, and no unusual token behavior. Any user can trigger it by calling the router instead of the pool directly. The precondition (router allowlisted) is the expected operational state for any curated pool that supports router-based swaps.

## Recommendation
1. **Router-side fix**: The router should encode the original caller's address in `extensionData` before calling `pool.swap()`. Extensions that need the real user identity can decode it from `extensionData`.
2. **Extension-side fix**: `SwapAllowlistExtension` could maintain a trusted-router registry and, when `sender` is a known router, decode the real user from `extensionData` for the allowlist check.
3. **Documentation / invariant guard**: At minimum, document that allowlisting the router in `SwapAllowlistExtension` grants all users swap access, and add a factory-level warning or a separate router-aware allowlist variant.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension (no users allowlisted by default).
2. Pool admin calls:
       swapExtension.setAllowedToSwap(pool, address(router), true)
   (necessary to allow any router-based swap on the curated pool).
3. Non-allowlisted user Bob calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
4. Router calls pool.swap() with msg.sender = router.
5. Pool calls _beforeSwap(router, bob, ...).
6. Extension evaluates:
       allowedSwapper[pool][router] == true  →  passes
7. Bob's swap executes on the curated pool.
   Direct pool call by Bob (pool.swap()) would have reverted NotAllowedToSwap.
```

The bypass is reachable on every router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) because all of them call `pool.swap()` with `msg.sender = router`.