Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end-user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the extension checks the router's address — not the end-user's. A pool admin who allowlists the router (required for any router-mediated swap to succeed) inadvertently grants every caller of the router unrestricted access to the curated pool.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // router address when called via router
    ...
);
```

`SwapAllowlistExtension.beforeSwap` checks that value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without threading the original caller:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The router's `msg.sender` (the end-user) is stored only in transient callback context for payment purposes (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`) and is never forwarded to the pool or extension. The extension therefore sees the router address as `sender` and never sees the actual user. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool where only specific addresses may trade. To permit any router-mediated swap, the admin must call `setAllowedToSwap(pool, router, true)`. Once done, every user who can call the public router bypasses the allowlist entirely. This is a direct admin-boundary break: the pool admin's curation policy is circumvented by an unprivileged path. The extension's stated invariant — "Gates `swap` by swapper address, per pool" — is broken for all router-mediated swaps.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who deploys a curated pool and also wants to support router users must allowlist the router, which triggers the bypass. No special privilege is required — any address can call the public router functions. The condition is a natural and expected configuration, not an edge case.

## Recommendation

Thread the original end-user address through the swap path so the extension can gate the economically relevant actor. The router should populate `extensionData` with `msg.sender` (the real user), and `SwapAllowlistExtension.beforeSwap` should decode and check that address when `extensionData` is present. Alternatively, add a `swapFor(address swapper, ...)` entry point on the router that passes the real user address as a dedicated field, and update `_beforeSwap` / the extension to consume it. The fix must ensure the user address cannot be spoofed by an arbitrary caller.

## Proof of Concept

```
1. Pool admin deploys MetricOmmPool with SwapAllowlistExtension as BEFORE_SWAP extension.
2. Pool admin calls:
       extension.setAllowedToSwap(pool, router, true);
   (Required for any router-mediated swap to pass the guard.)
3. Disallowed user (not in allowedSwapper) calls:
       router.exactInputSingle({pool: pool, ...});
4. Router calls pool.swap() — msg.sender = router.
5. Pool calls _beforeSwap(sender=router, ...).
6. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
7. Disallowed user's swap executes on the curated pool.

Expected: revert NotAllowedToSwap().
Actual:   swap succeeds; allowlist is bypassed.
```