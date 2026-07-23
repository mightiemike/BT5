Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of actual user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. Any pool admin who allowlists the router (the required step for allowlisted users to trade via the supported periphery) simultaneously grants every unpermissioned user the ability to bypass the per-user allowlist by calling any `exact*` function on the router.

## Finding Description
`CallExtension.callExtension` invokes extensions via a regular `.call()`, so inside `SwapAllowlistExtension.beforeSwap`, `msg.sender` is the pool and `sender` is the first argument forwarded from `ExtensionCalling._beforeSwap`.

In `MetricOmmPool.swap`, the first argument passed to `_beforeSwap` is `msg.sender` — the direct caller of `pool.swap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← caller of pool.swap()
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    ...
    params.extensionData
);
```

So `msg.sender` of `pool.swap()` = router address, and `sender` reaching the extension = router address.

The extension check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

resolves to `allowedSwapper[pool][router]`. Once the router is allowlisted, the check passes for **every** caller of the router, regardless of whether that caller is individually allowlisted. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` with the router as `msg.sender`.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to KYC'd or whitelisted addresses must also allowlist the router for those users to trade via the supported periphery. Once the router is in `allowedSwapper[pool]`, any unpermissioned user can call `router.exactInputSingle(...)` and the extension passes because it sees `sender = router` (allowlisted). The curated pool's access control is completely nullified for all router-mediated swaps, allowing unauthorized users to trade against LP funds on a pool explicitly configured to restrict access. This constitutes a broken core pool functionality and an admin-boundary break where the pool admin's access control configuration is bypassed by an unprivileged path.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported swap entry point for EOAs. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address — this is the expected operational configuration. The bypass is reachable by any unpermissioned user in any production deployment of a curated pool that uses the router, requiring no special privileges or unusual conditions.

## Recommendation
The extension must gate on the economically relevant actor — the address that initiated the transaction. The cleanest fix is to add an `originator` field to the swap extension data that the router populates with `msg.sender` before calling `pool.swap()`, and have `SwapAllowlistExtension.beforeSwap` decode and check that field when present. This requires a coordinated change to the router (populate `originator` in `extensionData`) and the extension (decode and check `originator` instead of or in addition to `sender`). Alternatively, document that the router must never be allowlisted and that allowlisted users must call `pool.swap()` directly — but this breaks the intended UX and is operationally fragile.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists alice (KYC'd user): allowedSwapper[pool][alice] = true
  - Pool admin allowlists router (to let alice use the router): allowedSwapper[pool][router] = true
  - bob is NOT allowlisted

Attack:
  1. bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  2. router calls pool.swap(bob, ...) — msg.sender to pool = router
  3. pool calls _beforeSwap(router, bob, ...)
  4. extension checks allowedSwapper[pool][router] → true → passes
  5. bob's swap executes on the curated pool despite not being allowlisted

Result: bob trades on a pool restricted to KYC'd users, bypassing the allowlist entirely.
The same path works via exactInput, exactOutputSingle, and exactOutput.
```