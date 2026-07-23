Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, allowing any caller to bypass per-user swap restrictions via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool sees `msg.sender = router`, not the end user. If the pool admin allowlists the router to support router-mediated swaps for their curated users, every unprivileged user can bypass the per-user restriction by calling any of the router's public `exact*` functions, causing unauthorized access to a pool designed to trade only with trusted counterparties.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmPool.swap()` passes `msg.sender` (the immediate caller of `swap()`) as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    ...
```

`ExtensionCalling._beforeSwap` then forwards this value directly to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L162-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, ...)
)
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router stores the original user as `payer` in transient storage and then calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-73
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

From the pool's perspective, `msg.sender = router`. The original user's identity is stored only in transient callback context as `payer` and is never forwarded to the pool as `sender`. So `beforeSwap` sees `sender = router`, not the end user.

This is structurally asymmetric with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly gates on `owner` (the economic beneficiary, an explicit parameter of `addLiquidity`) rather than `sender` (the immediate caller). The `swap()` function has no equivalent "swap owner" parameter — only `recipient` (output token destination) — so the only identity available to the extension is the immediate caller, which is the router when routing is used.

The exact wrong value is `allowedSwapper[pool][router]` being evaluated instead of `allowedSwapper[pool][end_user]`, causing the extension's access decision to be applied to the router address rather than the individual trader.

## Impact Explanation
A pool admin who wants to restrict swaps to a curated set of users (e.g., KYC'd counterparties) configures `SwapAllowlistExtension` and adds specific user addresses. To also support router-mediated swaps for those users, the admin adds the router to the allowlist. At that point, any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` and the allowlist check passes because `sender = router` is allowlisted. The per-user restriction is completely bypassed, allowing unauthorized traders to drain LP value from a pool designed to trade only with trusted counterparties — direct loss of LP principal.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle` with any pool address. The only precondition is that the pool admin has added the router to the allowlist, which is a natural and expected operational step when the admin wants to support router-mediated swaps for their allowlisted users. No privileged access, no malicious setup, and no non-standard tokens are required. The condition is reachable by any unprivileged address.

## Recommendation
The `beforeSwap` hook should gate on the end user's identity rather than the immediate caller. One approach mirrors the deposit allowlist: define a canonical "swap owner" concept (analogous to `owner` in `addLiquidity`) that the router explicitly forwards, and gate on that identity instead of `sender`. Concretely, the router could encode the original `msg.sender` into `extensionData` and the extension could decode and verify it. Alternatively, the extension can maintain a separate allowlist for approved routers and require that non-router callers are individually allowlisted, while router callers must supply a verifiable end-user identity in `extensionData`.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowlisted users.
3. Pool admin does **not** add `attacker` to the allowlist.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` — from the pool's perspective, `msg.sender = router`.
6. `MetricOmmPool.swap` passes `msg.sender` (router) as `sender` to `_beforeSwap`.
7. `ExtensionCalling._beforeSwap` forwards `sender = router` to `SwapAllowlistExtension.beforeSwap`.
8. `allowedSwapper[pool][router] == true` → check passes.
9. `attacker` successfully swaps in a pool they were never authorized to access.