Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the actual caller, allowing any user to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is the pool's own `msg.sender` at the time `swap` is called. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. Any pool admin who allowlists the router (required for router-mediated swaps to work) inadvertently grants every caller of the router — including explicitly excluded addresses — the ability to trade on a pool intended to be access-controlled.

## Finding Description
In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- pool's msg.sender, i.e. the router when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = router. The check resolves to `allowedSwapper[pool][router]`.

In `MetricOmmSimpleRouter.exactInputSingle`, the actual user's address is stored only in transient storage for the payment callback and is never forwarded to the pool:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The same applies to `exactInput` (L103-112), `exactOutputSingle` (L135-137), and `exactOutput` (L165-181) — all call `pool.swap(...)` with the router as `msg.sender`, and none forward the originating user's address as `sender`.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses. To allow those allowlisted users to trade through the periphery router, the admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router]` = `true`, and the extension's check passes for **every** caller of the router regardless of whether they are individually allowlisted. Non-allowlisted users can freely trade on a pool designed to be access-controlled, directly violating the pool admin's intended access policy and potentially causing loss of LP value on curated pools.

## Likelihood Explanation
The router is the primary user-facing swap entry point. Any pool admin who wants allowlisted users to trade conveniently through the router must allowlist the router address — this is the expected operational setup. Once the router is allowlisted (a near-certain precondition for any production curated pool), the bypass is reachable by any unprivileged user with no special preconditions, no privileged access, and no special tokens. It is repeatable on every swap.

## Recommendation
The pool must forward the originating user's address to the extension, not the immediate `msg.sender`. One approach: add an optional `swapper` field to the swap parameters that the router populates with `msg.sender` before calling the pool, and have the pool pass that value as `sender` to `_beforeSwap`. Alternatively, the extension can require that allowlisted pools only accept direct calls (no router intermediary) by checking that `sender == tx.origin`, though this breaks contract-based integrations. The cleanest fix is a trusted-forwarder pattern where the router encodes the originating user in `extensionData` and the extension reads it from there, verifying the caller is a trusted router.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — required for any router swap to succeed.
3. Pool admin does **not** allowlist Alice (`allowedSwapper[pool][alice]` = `false`).
4. Alice calls `router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
6. Pool calls `_beforeSwap(sender=router, ...)` → extension evaluates `allowedSwapper[pool][router]` = `true` → check passes.
7. Alice's swap executes successfully on a pool she was explicitly excluded from.

A Foundry integration test can confirm this by: deploying the extension and pool, configuring the allowlist with only the router address, then calling `router.exactInputSingle` from an address not in the allowlist and asserting the swap succeeds (no revert).