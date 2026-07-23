Audit Report

## Title
`SwapAllowlistExtension` Per-User Allowlist Bypassed via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is always the router address. Because EOAs cannot call `pool.swap()` directly (the pool immediately invokes `IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(...)`, which EOAs cannot implement), the pool admin must allowlist the router to enable any EOA swaps. Once the router is allowlisted, the per-user check is permanently satisfied for every caller, and any unprivileged user can swap on the restricted pool.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (enforced by `onlyPool`). `sender` is the first argument, which `MetricOmmPool.swap` sets to its own `msg.sender`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // immediate caller of pool.swap()
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

So `sender` passed to `beforeSwap` is the **router address**, not the end user. The extension checks `allowedSwapper[pool][router]`.

EOAs cannot call `pool.swap()` directly because the pool unconditionally calls back:

```solidity
// MetricOmmPool.sol L258
IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
```

EOAs have no code and cannot implement this callback, so they revert. This forces EOAs to use the router. For any router-mediated swap to succeed, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once set, `allowedSwapper[pool][router] = true` satisfies the check for every caller regardless of identity. The per-user restriction is permanently bypassed for all users routing through `MetricOmmSimpleRouter`.

The same structural issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap()` with the router as `msg.sender`.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) is rendered completely ineffective. Any unprivileged user can route through `MetricOmmSimpleRouter` and execute swaps on the restricted pool. This breaks the core access-control invariant the pool admin configured, exposing LP funds to unrestricted swap flow and violating the pool's intended operating model. This constitutes broken core pool functionality causing potential loss of funds.

## Likelihood Explanation

The only precondition is that the pool admin has allowlisted the router — which is the standard production setup for any pool that intends to support EOA swaps, since EOAs have no alternative path. No privileged access, no malicious setup, and no special token behavior is needed. Any user who knows the pool address can call `exactInputSingle` on the router and bypass the allowlist.

## Recommendation

The extension must gate on the **original user**, not the immediate pool caller. The router should forward `msg.sender` as an explicit field inside `extensionData`, and the extension should decode and verify it against `allowedSwapper`. This requires a coordinated extension+router design where the router encodes the real caller and the extension decodes it. Alternatively, pools using `SwapAllowlistExtension` should never allowlist the router, and a direct-call path with a permit-based callback wrapper (itself allowlisted per-user) should be provided for EOAs.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for EOA swaps
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended user
  - Bob (not allowlisted) is the attacker

Attack:
  1. Bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  2. Router calls pool.swap(bob, ...)  →  msg.sender in pool = router
  3. Pool calls _beforeSwap(router, bob, ...)
  4. Extension checks allowedSwapper[pool][router] = true  →  passes
  5. Bob's swap executes; he receives output tokens from the restricted pool

Result:
  - Bob swapped on a pool he was never allowlisted for
  - The allowlist check on sender (= router) is satisfied by the router's allowlist entry
  - Per-user restriction is completely bypassed
```