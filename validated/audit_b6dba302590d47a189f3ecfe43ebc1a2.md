Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of end-user, enabling allowlist bypass — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is always `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of the pool call, so the extension checks the router's allowlist status rather than the actual end-user's. Any pool admin who allowlists the router (required for allowlisted users to use the standard swap UI) simultaneously opens the allowlist to every unprivileged user who routes through the router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` verbatim as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // always the direct caller of pool.swap()
    recipient, ...
);
```

`ExtensionCalling._beforeSwap` encodes this as the first argument to `IMetricOmmExtensions.beforeSwap` (L162-175). `SwapAllowlistExtension.beforeSwap` then checks this `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` stores the actual user's address only in transient callback context for payment settlement and never forwards it to the pool:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
```

When Bob (not allowlisted) calls `exactInputSingle`, the router calls `pool.swap()` with `msg.sender = router`. The pool passes `sender = router` to the extension. If `allowedSwapper[pool][router] == true`, the check passes and Bob's swap executes. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

`DepositAllowlistExtension` correctly avoids this by checking `owner` (the position owner, always the actual user) rather than `sender` (the intermediary caller):

```solidity
// DepositAllowlistExtension.sol L32,38
function beforeAddLiquidity(address, address owner, ...)
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The swap path has no equivalent `owner`-style parameter — `pool.swap()` carries no originating user address — so the extension is structurally unable to check the actual end-user.

## Impact Explanation

A pool admin deploying `SwapAllowlistExtension` to restrict swaps (e.g., KYC-gated pool, restricted launch, emergency access control) faces an impossible configuration: not allowlisting the router breaks the standard UX for allowlisted users; allowlisting the router opens the pool to every unprivileged user. This constitutes a broken core pool access-control mechanism allowing unauthorized users to execute swaps on pools explicitly configured to restrict access, matching the "admin-boundary break bypassed by an unprivileged path" and "broken core pool functionality" impact categories.

## Likelihood Explanation

The router is the standard user-facing swap interface. Any pool admin who wants allowlisted users to have a normal UX must allowlist the router, which immediately enables the bypass. No special privileges are required: any EOA with a token approval can call `MetricOmmSimpleRouter.exactInputSingle` with a valid pool address. The bypass is reachable on every pool that has `SwapAllowlistExtension` configured and the router allowlisted, and is repeatable indefinitely.

## Recommendation

**Short term:** Populate an `originator` field in `extensionData` at the router level (e.g., `abi.encode(msg.sender)` prepended by the router), and have `SwapAllowlistExtension.beforeSwap` decode and check the originator when `sender` is a known router. Alternatively, add a dedicated originator parameter to the pool's `swap()` signature that the router populates with `msg.sender`.

**Long term:** Redesign `SwapAllowlistExtension.beforeSwap` to check the economically relevant actor — the entity whose tokens are being pulled (the payer) — rather than the syntactic `msg.sender` of the pool call. This mirrors how `DepositAllowlistExtension` correctly gates by `owner` rather than `sender`.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` extension.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowlisted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use the standard UI.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. `_beforeSwap(router, ...)` is dispatched; extension checks `allowedSwapper[pool][router] == true` → passes.
7. Bob's swap executes successfully on a pool he was never authorized to access.