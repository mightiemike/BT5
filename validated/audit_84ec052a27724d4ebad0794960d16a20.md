Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which is the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` resolves to the router's address, not the end user's address. A pool admin who allowlists the router to enable router-based swaps for legitimate users simultaneously opens the pool to every user who routes through the router, completely defeating the curation policy.

## Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the first argument forwarded by the pool — which is `msg.sender` at the time `pool.swap()` was called.

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract. Critically, the router passes `params.extensionData` directly — it does **not** encode its own `msg.sender` (the real end user) into `extensionData`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // ← user-supplied, not abi.encode(msg.sender)
    );
``` [3](#0-2) 

So the allowlist check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The pool admin faces an impossible choice:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | All router-based swaps revert, including those from individually allowlisted users |
| Router **allowlisted** | Every user can bypass the allowlist by routing through `MetricOmmSimpleRouter` |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps on the pool, receiving pool tokens at oracle-derived prices that the pool admin intended to reserve for vetted counterparties only. This constitutes broken core pool functionality for curated pools and a direct loss of the curation guarantee, matching the "Broken core pool functionality causing loss of funds" allowed impact.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical, publicly deployed periphery router. Any user who observes that a pool has a swap allowlist can trivially route through the router instead of calling the pool directly. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices. The bypass is deterministic and repeatable.

## Recommendation

The extension must identify the true end user, not the direct pool caller. The simplest safe fix: the router encodes `abi.encode(msg.sender)` as the first word of `extensionData` for allowlist-aware pools, and the extension decodes and checks that address instead of `sender`. Alternatively, the extension can check whether `sender` is a known trusted router and, if so, decode the real user from `extensionData`. A third option is to document and enforce at the factory level that pools using `SwapAllowlistExtension` must not be accessible via the generic router.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to allow Alice to use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router address.
6. Pool calls `_beforeSwap(router, recipient, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps on a pool he was explicitly excluded from, receiving pool tokens at oracle price.

Even without step 3, if the admin never allowlists the router, Alice also cannot use the router (step 4 would revert for her too), confirming the impossible-choice invariant described above.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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
