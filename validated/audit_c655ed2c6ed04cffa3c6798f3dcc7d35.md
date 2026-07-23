Audit Report

## Title
SwapAllowlistExtension Bypass via Router: `sender` Identity Mismatch Allows Unauthorized Swaps on Restricted Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the `pool.swap()` call — the router address when users route through `MetricOmmSimpleRouter`. A pool admin who allowlists the router to enable router-mediated swaps for their allowlisted users inadvertently opens the pool to all users, since any caller of the public router passes the check via `allowedSwapper[pool][router] == true`.

## Finding Description

**Root cause — pool passes `msg.sender` as `sender` to the extension:**

In `MetricOmmPool.swap()`, `_beforeSwap` receives `msg.sender` as the `sender` argument: [1](#0-0) 

**Extension checks that `sender` against the per-pool allowlist:**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key and `sender` (the immediate caller of `pool.swap()`) as the swapper identity: [2](#0-1) 

**Router calls `pool.swap()` directly, making itself the `sender`:**

For `exactInputSingle`, the router calls `pool.swap()` with no forwarding of the actual user: [3](#0-2) 

For `exactInput` (multi-hop), every hop is called from the router: [4](#0-3) 

In both cases, `msg.sender` of `pool.swap()` is the router address. The actual end user (`msg.sender` of the router call) is never forwarded to the pool or the extension.

**Identity mismatch table:**

| Scenario | `sender` seen by extension | Allowlist check |
|---|---|---|
| User calls `pool.swap()` directly | User address | Correct |
| User calls `MetricOmmSimpleRouter.exactInputSingle()` | Router address | Wrong identity |

**Contrast with `DepositAllowlistExtension`, which correctly gates the actual user:**

The deposit extension checks `owner` (the position owner, the actual user), not `sender` (the immediate caller): [5](#0-4) 

This asymmetry confirms the swap allowlist is checking the wrong identity. For deposits, `owner` is an explicit parameter separate from `sender`, allowing the extension to gate the actual user. For swaps, no equivalent "actual user" parameter exists — only `sender` (immediate caller) and `recipient`.

**Existing guards are insufficient:** There is no mechanism in the router to forward the actual user's address to the extension in a trusted way. The `extensionData` field is caller-supplied and unverified, so it cannot serve as a trusted identity channel without a trusted-router registry in the extension.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., a private institutional pool) loses that restriction entirely once the router is allowlisted. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle()` and execute swaps against LP funds without authorization. This constitutes a direct loss of LP principal through unauthorized trades at oracle-driven prices and a complete break of the pool's access-control invariant — matching the "broken core pool functionality causing loss of funds" and "admin-boundary break by an unprivileged path" allowed impacts.

## Likelihood Explanation

The bypass requires the pool admin to have allowlisted the router address. This is the natural and expected configuration for any pool that wants to support router-mediated swaps for its allowlisted users — there is no other way to enable router access while maintaining the allowlist. The attacker needs no special privileges: calling the public `MetricOmmSimpleRouter` is sufficient. The trigger is reachable by any unprivileged user on any pool that has both `SwapAllowlistExtension` and the router allowlisted.

## Recommendation

The extension must gate the actual end user, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user in `extensionData`**: Have the router encode `msg.sender` (the actual user) into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router. This requires the extension to maintain a trusted-router registry.

2. **Align with the deposit pattern**: Introduce a `swapper` field analogous to `owner` in the liquidity path — a field that always carries the economic principal regardless of who the immediate caller is — and have the pool populate it from the router's forwarded context.

Until fixed, pool admins should be warned that allowlisting the router address effectively opens the pool to all users, defeating the per-user allowlist.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin allowlists Alice (allowedSwapper[pool][alice] = true)
  - Pool admin also allowlists the router (allowedSwapper[pool][router] = true)
    so that Alice can use the router

Attack:
  - Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(recipient, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
  - Bob's swap executes against LP funds

Result:
  - Bob bypasses the per-user allowlist
  - Non-allowlisted user trades against LP principal
  - Pool access control is broken
```

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-39)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
