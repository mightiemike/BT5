Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual end-user, allowing allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is the `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. A pool admin who allowlists the router address to enable router-mediated swaps inadvertently grants unrestricted swap access to every public caller of the router, defeating the sole purpose of the extension.

## Finding Description
`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the value the pool passes from its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
```

The pool sees `msg.sender = router`, so `_beforeSwap(router, ...)` is called, and the extension evaluates `allowedSwapper[pool][router]`. The actual end-user (`msg.sender` of `exactInputSingle`) is never checked. The same path exists for `exactInput`, `exactOutputSingle`, and `exactOutput`. The router does not encode the real caller into `extensionData`, and the extension does not decode `extensionData` to recover the actual user — there is no existing mitigation.

## Impact Explanation
**Medium — broken allowlist gate, unauthorized swap access.** A pool admin who deploys `SwapAllowlistExtension` and allowlists the router address to permit router-mediated swaps inadvertently opens the gate to every public caller. Any address — regardless of whether it appears in `allowedSwapper` — can execute swaps on the restricted pool by routing through `MetricOmmSimpleRouter`. This breaks the core pool invariant that only approved counterparties may trade, which is the sole purpose of the extension. This constitutes broken core pool functionality (swap allowlist) causing unauthorized access to a restricted pool.

## Likelihood Explanation
**Medium.** The admin must explicitly allowlist the router for the bypass to be reachable. This is a realistic and natural configuration: a pool operator who wants to allow router-mediated swaps for their approved users will allowlist the router address, not realising that doing so grants access to every caller of the public router. The router is a well-known, publicly deployed contract, so any user can invoke it without any special privilege. No special on-chain conditions are required beyond this one admin action.

## Recommendation
The extension must gate the economic actor (the end-user), not the intermediary. Viable approaches:
1. **Pass the real caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router convention and the extension to verify the caller is a known router before trusting the decoded address.
2. **Check both `sender` and a caller field**: If `sender` is a known router, decode the actual user from `extensionData`; otherwise check `sender` directly.
3. **Document that the allowlist only works for direct pool calls**: If router-mediated allowlisting is intentionally unsupported, NatDoc and admin UI must make this explicit so operators do not allowlist the router.

## Proof of Concept
1. Admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, routerAddress, true)` — intending to permit router-mediated swaps for approved users.
3. Non-allowlisted user `C` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
5. Pool calls `_beforeSwap(sender = router, ...)`.
6. `SwapAllowlistExtension.beforeSwap(sender = router, ...)` evaluates `allowedSwapper[pool][router]` → `true`.
7. Swap executes successfully. User `C` has bypassed the allowlist without being individually approved.

Foundry test sketch:
```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. setAllowedToSwap(pool, address(router), true)
// 3. vm.prank(unprivilegedUser);
//    router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
// 4. Assert: swap succeeds despite unprivilegedUser not being in allowedSwapper
```