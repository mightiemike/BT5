Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of Originating User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of `pool.swap`. When `MetricOmmSimpleRouter` is used, `sender` is the router address, not the originating user. A pool admin who allowlists the router to enable router-based swaps for legitimate users inadvertently grants swap access to every user who routes through it, rendering the per-user allowlist unenforceable for any router-mediated swap.

## Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool; `sender` is whatever address called `pool.swap`. The pool always passes its own `msg.sender` as `sender`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient, ...
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol L71-80
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

The originating user's address (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment purposes and is never forwarded to the pool or extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originating_user]`.

Contrast with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly gates on `owner` (the economic beneficiary of the position), not `sender` (the caller):

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The swap extension applies the analogous check to the wrong actor. Because the router is a single shared address, the allowlist collapses to a binary: either the router is allowlisted (all users can swap through it) or it is not (no allowlisted user can use the router). Individual-user granularity is lost entirely for router-mediated swaps.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties loses that protection for any user who routes through `MetricOmmSimpleRouter`. Once the router is allowlisted (a necessary step for any allowlisted user to benefit from slippage protection, deadline enforcement, or multi-hop routing), non-allowlisted users can execute swaps against the pool. LPs are exposed to the exact adverse-selection risk the allowlist was designed to prevent, constituting a direct loss of LP assets above Sherlock thresholds when the pool carries meaningful liquidity. This matches the "Admin-boundary break" and "Broken core pool functionality causing loss of funds" allowed impact categories.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard supported periphery swap path. Any pool admin who allowlists individual users and also wants those users to benefit from router features (slippage protection, deadline, multi-hop) must allowlist the router address. The protocol provides no in-band signal that doing so opens the gate to all users. The bypass requires no special privilege — any address can call `MetricOmmSimpleRouter.exactInputSingle` with the target pool. The condition is reachable on every pool that has both `SwapAllowlistExtension` configured and the router allowlisted.

## Recommendation

Gate the swap allowlist on the economic actor, not the immediate caller. Two options:

1. **Check `recipient` instead of `sender`**: The recipient receives output tokens and is the closest on-chain proxy for the economic actor in a single-hop swap. This is consistent with how `DepositAllowlistExtension` gates on `owner`.

2. **Require the router to attest the originator**: Add an optional `address originator` field to `extensionData` that the router populates with `msg.sender`, and have the extension verify it against a registry of approved routers that are trusted to attest the originator faithfully.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — Alice is a trusted counterparty.
3. Admin calls `setAllowedToSwap(pool, router, true)` — to let Alice use the router for slippage-protected swaps.
4. Bob (never allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. The router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. The pool calls `_beforeSwap(router, bob, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true` → guard passes.
8. Bob's swap executes against the curated pool. The individual allowlist is fully bypassed.