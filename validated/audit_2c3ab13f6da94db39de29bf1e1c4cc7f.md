Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which the pool always sets to `msg.sender` — the immediate caller of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This produces two concrete failure modes: (1) if the router is allowlisted, any non-allowlisted user bypasses the curated-pool gate; (2) if only specific users are allowlisted, those users cannot use the standard router path.

## Finding Description
In `MetricOmmPool.swap`, the pool passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap`, it passes no identity-forwarding mechanism for the original caller — `msg.sender` stored in transient storage is used only for the payment callback, not forwarded to the pool as a `sender` parameter: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router — not the actual user: [3](#0-2) 

The pool's `swap` signature has no explicit `swapper` parameter; the pool always derives the sender from `msg.sender`, so the router has no way to forward the original caller's identity through the standard call path: [4](#0-3) 

**Failure mode 1 (bypass):** Admin allowlists the router (e.g., to allow all router-mediated swaps while blocking direct pool calls). Any non-allowlisted user calls `router.exactInputSingle` → pool sees `sender=router` → `allowedSwapper[pool][router]=true` → swap succeeds. The per-user gate is fully bypassed.

**Failure mode 2 (broken path):** Admin allowlists specific user addresses but not the router. An allowlisted user calls `router.exactInputSingle` → pool sees `sender=router` → `allowedSwapper[pool][router]=false` → revert. The user must call `pool.swap` directly, making the supported periphery path unusable for curated pools.

## Impact Explanation
Failure mode 1 is a direct policy bypass: a non-allowlisted actor executes swaps on a curated pool the admin intended to restrict, breaking the pool's curation invariant and potentially exposing the pool to actors it was designed to exclude. Failure mode 2 renders the primary user-facing swap entrypoint (`MetricOmmSimpleRouter`) unusable for allowlisted users on curated pools — broken core pool swap functionality. Both impacts fall within the allowed gate: broken core pool functionality causing unusable swap flows, and admin-boundary break where an unprivileged path bypasses the per-user allowlist check.

## Likelihood Explanation
`SwapAllowlistExtension` is a production periphery extension designed for curated pools, and `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool deploying both will encounter one of the two failure modes. Failure mode 2 is triggered by any allowlisted user who uses the router (the default path) — no special attacker capability required. Failure mode 1 requires the admin to allowlist the router address, which is a plausible configuration when the admin intends to allow all router users while blocking direct pool calls.

## Recommendation
The pool's `swap` function should accept an explicit `swapper` parameter that the router forwards, or `SwapAllowlistExtension` should decode the original caller from `extensionData` with a trusted-router check. A simpler mitigation is to have the router encode `msg.sender` into `extensionData` and have the extension decode it when the caller is a trusted router. Alternatively, document and enforce at the factory level that `SwapAllowlistExtension` is incompatible with router-mediated swaps.

## Proof of Concept
```solidity
// Failure mode 1: allowlist bypass
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// attacker is NOT in allowedSwapper[pool][attacker]
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({ pool: address(pool), ... }));
// Extension checks allowedSwapper[pool][router] = true → swap succeeds
// Attacker bypasses the curated-pool allowlist

// Failure mode 2: broken periphery path
swapExtension.setAllowedToSwap(address(pool), allowlistedUser, true);
vm.prank(allowlistedUser);
router.exactInputSingle(...);
// Extension checks allowedSwapper[pool][router] = false → reverts
// allowlistedUser must call pool.swap directly — router path is broken
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
