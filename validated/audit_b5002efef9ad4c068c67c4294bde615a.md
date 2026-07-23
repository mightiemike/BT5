Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which is `msg.sender` of the pool's `swap` call. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. If the pool admin allowlists the router to enable router-mediated swaps for permitted users, every unpermitted user can bypass the allowlist by routing through the same public router, since `allowedSwapper[pool][router] == true` for all callers.

## Finding Description
`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool via `_callExtensionsInOrder`) and `sender` is the first argument forwarded from the pool's `swap` function.

`MetricOmmPool.swap` passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    ...
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

So the pool's `msg.sender` is the router, and the extension receives `sender = router`. The allowlist lookup becomes `allowedSwapper[pool][router]`.

**Root cause:** The admin intends to gate individual swappers. To also permit router-mediated swaps for those same users, the admin must add the router to the allowlist via `setAllowedToSwap(pool, router, true)`. The moment the router is allowlisted, `allowedSwapper[pool][router] == true` for every caller — any unpermitted user can route through the public router and the check passes unconditionally.

**Contrast with `DepositAllowlistExtension`:** The deposit guard correctly checks `owner` (the position beneficiary), which is passed as a separate argument independent of `msg.sender`, so the deposit guard remains correct regardless of whether a liquidity-adding intermediary is used.

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

## Impact Explanation
A curated pool relying on `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC-verified addresses, trusted market makers, or regulatory-compliant participants) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized swappers gain full access to the pool's liquidity at oracle prices, breaking the admin-configured access boundary. This is an admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses a pool-admin-enforced guard.

## Likelihood Explanation
The bypass is reachable whenever the pool admin has allowlisted the router — a natural and expected configuration step for any pool that wants to support the standard periphery swap path. The router is a public, permissionless contract. No special privilege or malicious setup is required beyond the admin's own routine allowlist entry. The bypass is repeatable by any address.

## Recommendation
Gate on the economically relevant actor, not the immediate caller. Two options:

1. **Pass the original user through the router.** Have `MetricOmmSimpleRouter` encode the original `msg.sender` in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value instead of `sender`. This requires a coordinated change to both contracts.

2. **Maintain a registry of trusted routers.** When `sender` is a trusted router, require an additional user-identity field in `extensionData`. This is the pattern used by Uniswap v4's `UniversalRouter`.

At minimum, document that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and warn pool admins accordingly, but a code-level fix is strongly preferred.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)   // alice is permitted
  admin calls setAllowedToSwap(pool, router, true)  // router added so alice can use it

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})

  Router calls pool.swap(...) → pool passes msg.sender=router as `sender`
  Extension checks: allowedSwapper[pool][router] == true  ✓
  Bob's swap executes on the curated pool — allowlist fully bypassed.
```

Foundry test outline:
1. Deploy pool with `SwapAllowlistExtension` attached.
2. `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)`.
3. Call `MetricOmmSimpleRouter.exactInputSingle` from `bob` (not allowlisted).
4. Assert the swap succeeds (no `NotAllowedToSwap` revert), confirming the bypass.