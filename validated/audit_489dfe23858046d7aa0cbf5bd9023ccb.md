Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any non-allowlisted user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of the pool's `swap()` call — the router contract address when a user routes through `MetricOmmSimpleRouter`. If the pool admin allowlists the router (required for allowlisted users to use router functionality), any non-allowlisted user can call the router and pass the extension check because `allowedSwapper[pool][router]` is `true`, completely defeating the per-user access control.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // router address when called via router
  ...
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (validated by `onlyPool`) and `sender` is the router's address. The `onlyPool` modifier in `BaseMetricExtension` only validates that the caller is a registered pool — it does not validate which user initiated the action:

```solidity
// BaseMetricExtension.sol L19-23
modifier onlyPool() {
  if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
    revert OnlyPool(msg.sender, FACTORY);
  }
```

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly without forwarding the original caller's identity:

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

**Exploit path**: Admin sets `allowedSwapper[pool][user_A] = true` and `allowedSwapper[pool][router] = true` (required for `user_A` to use the router). Attacker (not in allowlist) calls `router.exactInputSingle(...)`. Router calls `pool.swap(...)` with `msg.sender = router`. Pool calls `_beforeSwap(router, ...)`. Extension evaluates `allowedSwapper[pool][router] == true` → check passes → swap executes for the attacker.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Impact Explanation
Any non-allowlisted user can execute swaps on a pool restricted to a specific set of addresses. This breaks the core access-control invariant enforced by `SwapAllowlistExtension`. For pools gating institutional-only or regulatory-gated liquidity, unauthorized swaps can drain LP assets at oracle-derived prices, cause pool insolvency, or violate compliance requirements the allowlist was designed to enforce. This constitutes broken core pool functionality causing direct loss of LP assets and an admin-boundary break where the per-user gate is bypassed by an unprivileged path.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard publicly deployed periphery entry point. The bypass requires no privileged access, no special setup, and no non-standard token behavior. The condition enabling the bypass — the router being allowlisted — is the natural operational state for any pool whose allowlisted users need router functionality (slippage protection, multi-hop routes). Any user who discovers the allowlist can trivially route through the router.

## Recommendation
The extension must check the original user identity, not the intermediary. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it against the allowlist. Requires a registry of trusted routers so the extension only accepts attested identities from known routers.

2. **Trusted router registry with real-user attestation**: Add a registry of trusted routers; for trusted routers, decode the real user from `extensionData` and check that address against `allowedSwapper`; for direct callers, check `sender` as today.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin: setAllowedToSwap(pool, user_A, true)
  - Pool admin: setAllowedToSwap(pool, router, true)  // required for user_A to use router

Attack:
  - attacker (not in allowlist) calls:
      router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})
  - Router calls pool.swap(...) → msg.sender at pool = router
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes — attacker bypassed the allowlist
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
