Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end-user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router — a necessary step for any allowlisted user to reach the pool via the router — inadvertently grants swap access to every caller of the router, regardless of individual allowlist status.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` evaluates the allowlist against that `sender` value, using `msg.sender` (the pool) as the mapping key: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and all other swap entry points) calls `pool.swap()` directly with no mechanism to forward the original `msg.sender`: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

When a user calls the pool directly, `sender = user` and the check is correct. When the same user routes through `MetricOmmSimpleRouter`, `sender = router`. The allowlist lookup becomes `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router, the check passes for every caller of the router. The `setAllowedToSwap` setter has no mechanism to distinguish a pass-through router from a permitted end user: [6](#0-5) 

## Impact Explanation

A non-allowlisted user can swap against a curated pool — bypassing the intended per-user access-control boundary — by routing through the public `MetricOmmSimpleRouter`. Curated pools are deployed to restrict counterparties (e.g., KYC-gated, institutional-only, or strategy-specific). Unauthorized swaps drain LP-owned inventory at oracle-quoted prices, constituting direct loss of LP principal. This matches "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path."

## Likelihood Explanation

The bypass requires the router to be allowlisted. Any pool that intends to support router-based swaps for its allowlisted users must allowlist the router, making this a natural and common configuration. The triggering action — routing through the public `MetricOmmSimpleRouter` — is fully unprivileged and requires no special access. The side-effect (universal bypass) is non-obvious from the API, making the misconfiguration easy to introduce unintentionally.

## Recommendation

The allowlist must be keyed on the economically relevant actor — the end user — not the immediate caller. Two viable approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` inside `extensionData`, and update `SwapAllowlistExtension.beforeSwap` to decode and check that field when the immediate caller is a known, trusted router.

2. **Sender-override parameter**: Add an optional `effectiveSender` parameter to `MetricOmmPool.swap()` that trusted routers can populate; the pool passes this to extensions instead of `msg.sender`. The pool must verify the caller is an authorized router before accepting a non-self `effectiveSender`.

Either approach must ensure the override path is itself gated so that arbitrary callers cannot spoof an allowlisted identity.

## Proof of Concept

```
Setup
─────
1. Pool P is deployed with SwapAllowlistExtension E configured.
2. Admin calls E.setAllowedToSwap(P, alice, true)   // alice is KYC'd
3. Admin calls E.setAllowedToSwap(P, router, true)  // router allowlisted so alice can use it
4. bob is NOT allowlisted.

Attack
──────
5. bob calls MetricOmmSimpleRouter.exactInputSingle(... pool=P ...)
   → router calls P.swap(recipient=bob, ...)          // msg.sender to pool = router
   → pool calls _beforeSwap(sender=router, ...)
   → SwapAllowlistExtension checks allowedSwapper[P][router] == true  ✓
   → swap executes; bob receives token output from LP inventory

Result
──────
bob, a non-allowlisted user, successfully swaps against a curated pool,
bypassing the per-user allowlist entirely.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-165)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-20)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }
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
