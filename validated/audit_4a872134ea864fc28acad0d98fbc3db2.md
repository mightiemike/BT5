Audit Report

## Title
SwapAllowlistExtension gates the router's address instead of the end user, allowing any user to bypass the per-pool swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes the pool's `msg.sender`, so the extension checks whether the **router** is allowlisted rather than the **end user**. If the router is allowlisted (the only way to let allowed users reach the pool via the router), every user — including disallowed ones — can bypass the individual allowlist by routing through the router.

## Finding Description

**Root cause — `SwapAllowlistExtension.beforeSwap`** checks `sender` (the first argument), where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

**`MetricOmmPool.swap`** passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(); the router when routed
    recipient,
    ...
);
```

**`MetricOmmSimpleRouter.exactInputSingle`** calls `pool.swap()` directly, making itself the pool's `msg.sender`. Critically, it does **not** encode the original user into `extensionData`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // ← user-supplied, not router-injected
    );
```

Full call chain:
```
DisallowedUser → Router.exactInputSingle()
              → Pool.swap()          [pool.msg.sender = router]
              → _beforeSwap(sender = router, ...)
              → Extension.beforeSwap(sender = router, ...)
              → allowedSwapper[pool][router] == true → PASSES
```

The `DepositAllowlistExtension` does **not** share this flaw: it checks `owner` (the second parameter), which the pool passes as the position owner regardless of who the direct caller is, so routing through a router does not affect the check.

## Impact Explanation

This is an **admin-boundary break**: a pool admin who deploys a curated pool with `SwapAllowlistExtension` faces an inescapable dilemma. If the router is not allowlisted, allowed users cannot use the router at all. If the router is allowlisted (the natural admin action to enable the standard UX path), every user — including disallowed ones — can bypass the individual allowlist by routing through the router. There is no configuration that achieves the intended invariant ("only allowlisted users may swap, including via the router"). The exact wrong value is `allowedSwapper[pool][router]` being checked instead of `allowedSwapper[pool][endUser]`, causing the extension's access-control decision to be incorrect for all router-mediated swaps.

## Likelihood Explanation

Medium. The bypass requires the router to be on the allowlist. Pool admins who want allowed users to be able to use the router (the standard UX path) will naturally add the router to the allowlist, inadvertently enabling the bypass for all users. The router is a public, permissionless contract, so any disallowed user can call it without any special privilege.

## Recommendation

The extension must check the **economically relevant actor** — the end user — not the direct caller of `pool.swap()`. Two options:

1. **Pass the original user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.
2. **Align with the deposit allowlist pattern**: Have the pool pass a separate "originator" field (analogous to `owner` in `addLiquidity`) that the router fills with the end user's address, and have the extension check that field instead of `sender`.

## Proof of Concept

```solidity
// Pool admin sets up a curated pool with SwapAllowlistExtension
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool deployed with ext as beforeSwap extension

// Admin allowlists the router so that allowed users can use it
ext.setAllowedToSwap(pool, address(router), true);

// Disallowed user (not individually allowlisted) bypasses the guard:
// router.msg.sender = disallowedUser
// pool.msg.sender   = router  ← extension sees this
router.exactInputSingle(ExactInputSingleParams({
    pool:      pool,
    recipient: disallowedUser,
    ...
}));
// Extension checks allowedSwapper[pool][router] == true → passes
// Disallowed user successfully swaps on the curated pool
```