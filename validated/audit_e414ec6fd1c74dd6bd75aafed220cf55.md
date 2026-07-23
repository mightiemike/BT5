Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router — which is required for any router-mediated swap to succeed — every user, including non-allowlisted ones, can bypass the allowlist entirely by routing through the router.

## Finding Description
In `SwapAllowlistExtension.beforeSwap`, the check is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct key) and `sender` is the address passed by the pool as the swap initiator. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,  // sender = whoever called pool.swap()
    recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension. In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The originating user's address (`msg.sender` of `exactInputSingle`) is stored only in transient storage for the payment callback and is never forwarded to the pool or the extension. The pool's `msg.sender` is the router, so the extension receives `sender = router`. The `extensionData` field passed through the router is ignored by `SwapAllowlistExtension` (the last `bytes calldata` parameter is unnamed and unused), so there is no existing mechanism to recover the originating user's identity.

This creates an irreconcilable conflict: if the admin does not allowlist the router, allowlisted users cannot use the router at all. If the admin does allowlist the router (the natural step to enable router-mediated swaps), every user — including non-allowlisted ones — can bypass the allowlist by routing through the router, because the extension sees `allowedSwapper[pool][router] = true`. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Impact Explanation
Any user can bypass a pool's `SwapAllowlistExtension` by calling `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on a pool that has allowlisted the router. The allowlist — the pool admin's primary mechanism for restricting access (e.g., KYC, institutional-only pools, regulatory compliance) — is rendered ineffective. Non-allowlisted users can execute swaps at oracle-derived prices on a pool intended to be restricted, violating the pool's access policy and potentially draining LP funds or enabling unauthorized price exposure.

## Likelihood Explanation
The pool admin must allowlist the router for router-mediated swaps to work at all. This is a natural and expected configuration step for any production pool that wants to support the standard periphery. The admin is unlikely to realize that allowlisting the router grants unrestricted access to all users, since the extension's NatSpec states it "Gates `swap` by swapper address, per pool" — implying user-level granularity. The bypass requires only a standard router call, which is the most common user-facing entry point, and is repeatable by any address.

## Recommendation
The `SwapAllowlistExtension` must check the originating user's address, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pool-level fix:** The pool should pass the originating user's address as a dedicated parameter to the extension hook (separate from `sender`, which is the immediate caller). The hook signature would carry both the immediate caller and the economic actor.
2. **Extension-level fix:** The router encodes the originating user's address in `extensionData`; the extension decodes and verifies it, while also verifying that `msg.sender` (the pool's caller) is a trusted router registered with the factory. This prevents spoofing while preserving user-level granularity.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
6. Pool calls `extension.beforeSwap(router, ...)` — `sender` = router.
7. Extension checks `allowedSwapper[pool][router]` = `true` → passes without revert.
8. Attacker's swap executes on the restricted pool, bypassing the allowlist entirely.