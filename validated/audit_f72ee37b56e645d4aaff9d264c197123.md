Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the actual user. An admin who allowlists the router to enable router-based swaps for permitted users inadvertently opens the pool to every user on the router, completely defeating the per-user gate.

## Finding Description

**Root cause — `SwapAllowlistExtension.beforeSwap`:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the address forwarded by the pool. The check is therefore `allowedSwapper[pool][sender]`.

**`MetricOmmPool.swap` passes its own `msg.sender` as `sender`:**

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient, ...
);
```

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly:**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // ← raw user bytes, extension never decodes for identity
    );
```

The router passes `params.extensionData` through unchanged; `SwapAllowlistExtension.beforeSwap` never decodes it to recover the actual user. There is no existing guard that distinguishes a router call from a direct call or that recovers the end-user identity.

**Full exploit call chain:**

```
userB → MetricOmmSimpleRouter.exactInputSingle(...)
           → pool.swap(recipient, ..., extensionData)   // msg.sender = router
                → _beforeSwap(sender=router, ...)
                     → SwapAllowlistExtension.beforeSwap(sender=router)
                          → allowedSwapper[pool][router] == true → passes
```

**Bypass scenario:**

1. Admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC'd addresses.
2. Admin allowlists userA: `setAllowedToSwap(pool, userA, true)`.
3. Admin allowlists the router so userA can use it: `setAllowedToSwap(pool, router, true)`.
4. Unapproved userB calls `router.exactInputSingle(...)`. The extension sees `sender = router`, finds `allowedSwapper[pool][router] = true`, and passes. UserB swaps successfully despite never being allowlisted.

Step 3 is the natural, expected admin action. Without it, even allowlisted users cannot use the router. Yet it silently opens the pool to all router users. There is no mechanism to allowlist specific users *for router-based swaps*; the only choices are "allowlist the router (all users pass)" or "don't allowlist the router (no router user passes)."

## Impact Explanation

Any user can bypass the swap allowlist by routing through `MetricOmmSimpleRouter` once the admin has allowlisted the router. The allowlist is the sole access-control layer for pools that require it (e.g., permissioned institutional pools, KYC-gated pools). A bypass allows unauthorized parties to execute swaps, draining pool liquidity at oracle-anchored prices and causing direct loss of LP principal. This constitutes a broken core pool functionality causing loss of funds and an admin-boundary break where the pool admin's intended per-user restriction is bypassed by an unprivileged path.

## Likelihood Explanation

The admin action that triggers the bypass (allowlisting the router) is the natural, expected step to enable router-based swaps for permitted users. The documentation and interface give no indication that doing so opens the pool to all users. Any pool operator who deploys a `SwapAllowlistExtension` and also wants router support will hit this path. The bypass itself requires no special privileges or unusual conditions once the router is allowlisted — any unprivileged address can call `router.exactInputSingle`.

## Recommendation

The extension must gate the economically relevant actor — the end-user — not the intermediary router. Two viable approaches:

1. **Router passes the real user in `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` for each hop, and `SwapAllowlistExtension` decodes and checks that address instead of (or in addition to) `sender`.

2. **Check `sender` only for direct calls; require `extensionData` attestation for router calls**: The extension can detect that `sender` is a known router and require a signed or encoded user identity in `extensionData`.

Either way, the extension must not treat the router address as the identity to gate.

## Proof of Concept

```solidity
// 1. Pool admin sets up allowlist for userA only
swapAllowlist.setAllowedToSwap(pool, userA, true);

// 2. Admin allowlists the router so userA can use it
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// 3. userB (NOT allowlisted) calls the router
vm.prank(userB);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: token0,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: userB,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// ↑ succeeds — userB bypassed the allowlist
// Extension saw sender=router, allowedSwapper[pool][router]=true → no revert
```

**Relevant code locations:**

- `SwapAllowlistExtension.beforeSwap` checks `sender` (the router) instead of the actual user: [1](#0-0) 

- `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [2](#0-1) 

- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` as `msg.sender` (the router), passing `extensionData` unchanged without encoding the real user: [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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
