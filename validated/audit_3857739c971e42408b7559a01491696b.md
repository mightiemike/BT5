Audit Report

## Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter: Router Address Replaces User Identity in Allowlist Check — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument — the direct caller of `pool.swap()` — against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][original_user]`. A pool admin who allowlists the router to enable router-mediated swaps for their curated user set inadvertently opens the pool to every user who calls the router, completely defeating the allowlist.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`. This `sender` value originates from `MetricOmmPool.swap`, which passes its own `msg.sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
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

The pool's `msg.sender` is the router address, so `sender = router` reaches the extension. The extension then checks `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (a natural step to enable router-mediated swaps for their curated users), the check passes for **any** caller of the router — not just the intended allowlisted users. The same substitution occurs for `exactInput`, `exactOutputSingle`, and `exactOutput`.

No existing guard in the extension or pool prevents this substitution. The extension has no mechanism to distinguish between a direct swap by an allowlisted user and a router-mediated swap by an arbitrary user.

## Impact Explanation

A curated pool's swap allowlist is completely bypassed for any user who routes through `MetricOmmSimpleRouter`. The admin cannot simultaneously (a) allow router-mediated swaps and (b) restrict swaps to specific users. Any non-allowlisted user can call `router.exactInputSingle()` and execute swaps on the curated pool, violating the admin-configured access boundary. This is an admin-boundary break: an unprivileged path (the public router) bypasses a configured guard, allowing unauthorized users to trade on pools explicitly restricted to a curated set.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Pool admins who want to enable router-mediated swaps for their allowlisted users will naturally allowlist the router address. The bypass is then trivially reachable by any user with no special privileges, no capital requirements beyond the swap amount, and no time constraints. The scenario is repeatable indefinitely.

## Recommendation

The `SwapAllowlistExtension` must check the original end-user identity, not the direct caller of `pool.swap()`. Viable approaches:

1. **Pass original user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. Requires coordinated change to router and extension.
2. **Reject known router addresses**: The extension can maintain a registry of known routers and revert when `sender` is a router, forcing direct-only swaps on curated pools.
3. **Document the incompatibility**: If the design intent is that `sender` is always the direct caller, document clearly that allowlisting the router opens the pool to all router users, and that curated pools must not allowlist the router.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin calls setAllowedToSwap(pool, alice, true)   // allowlist alice
3. Admin calls setAllowedToSwap(pool, router, true)  // allowlist router so alice can use it
4. charlie (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(); pool sees msg.sender = router.
6. _beforeSwap passes sender = router to SwapAllowlistExtension.
7. Extension checks allowedSwapper[pool][router] → true → passes.
8. charlie's swap executes on the curated pool, bypassing the allowlist.
```

The root cause is confirmed at:
- [1](#0-0) 
- [2](#0-1) 
- [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
