Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist, where `sender` is always the immediate caller of `pool.swap`. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` inside `pool.swap`, so the extension checks the router address rather than the end-user. If the pool admin allowlists the router to enable router-mediated swaps for curated users, every unpermissioned user can bypass the allowlist by routing through the same router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap`, the pool always passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,  // whoever called pool.swap()
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly without encoding the real end-user into `extensionData`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData  // user-supplied, no real sender encoded by router
    );
```

`msg.sender` inside `pool.swap` is therefore the **router address**, not the end user. The extension receives `sender = address(router)` and checks `allowedSwapper[pool][router]`. The router stores the real `msg.sender` only in transient callback context for payment purposes, never in `extensionData` for the extension to read.

This creates an irreconcilable conflict for the pool admin:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | All router-mediated swaps revert, even for allowlisted users |
| **Allowlist the router** | Every user — allowlisted or not — can swap by routing through the router |

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of counterparties (e.g., KYC'd users, institutional partners). Once the router is allowlisted — a natural and expected operational step — any unpermissioned user can execute swaps on the curated pool by calling `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on `MetricOmmSimpleRouter`. This results in unauthorized users draining LP assets at oracle-derived prices from a pool designed to serve only specific counterparties, constituting direct loss of LP principal and complete curation failure. This is broken core pool functionality causing loss of funds, matching the allowed impact gate.

## Likelihood Explanation
Medium. The trigger requires the pool admin to allowlist the router address. This is a natural operational step — the router is the primary user-facing interface and allowlisted users need it to swap. A pool admin who wants to allow their curated users to use the router will inevitably add the router to the allowlist, inadvertently opening the pool to all users. The admin action is semi-trusted but is the expected and documented usage pattern. No special attacker capability is required beyond calling the public router.

## Recommendation
The extension must gate on the economically relevant actor — the end user — not the intermediary. The cleanest fix is to have the router encode the real `msg.sender` in `extensionData` and have the extension decode and check it when present. Alternatively, add a dedicated `realSender` field to the extension data that the router populates with its own `msg.sender`, and have the extension prefer that field over the `sender` argument when present. This requires a trust assumption that the router is the only allowed intermediary, which can be enforced by also allowlisting the router address as a trusted forwarder separately from end-user entries.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` wired as the `beforeSwap` hook.
2. Pool admin allowlists `alice` as a permitted swapper: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin also allowlists the router so that `alice` can use it: `setAllowedToSwap(pool, router, true)`.
4. `bob` (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` — `msg.sender` inside the pool is `router`.
6. The pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `bob` successfully swaps on the curated pool despite never being allowlisted.