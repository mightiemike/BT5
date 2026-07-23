Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. Any pool admin who allowlists the router to support normal UX inadvertently grants every caller of that public router unrestricted access to the curated pool, bypassing the per-user allowlist entirely.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- this is the router when called via router
  recipient,
  ...
  extensionData
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension. The extension then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is the router address. When `MetricOmmSimpleRouter.exactInputSingle` calls the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The pool's `msg.sender` is the router, so the extension evaluates `allowedSwapper[pool][router]`. A pool admin who wants allowlisted users to use the router must set `allowedSwapper[pool][router] = true`. Once set, the check passes for **every** caller of the router regardless of their individual allowlist status. The same flaw applies to `exactInput`, `exactOutput`, and `exactOutputSingle`.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties is fully bypassed. Any unpermitted user calls `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool. The extension sees `sender = router`, which is allowlisted, and permits the swap. The unpermitted user executes trades on a pool designed to exclude them, receiving output tokens and depleting LP reserves reserved for permitted counterparties. This is a direct loss of LP assets and a broken core pool invariant (access-controlled swap execution), qualifying as a High-severity finding under Sherlock criteria.

## Likelihood Explanation

The router is the standard public entry point for swaps. Any pool admin who wants their allowlisted users to be able to use the router (the normal UX path) must allowlist the router address. This is the expected operational configuration for any curated pool that does not require users to call the pool directly. The attacker needs no special privilege: they call a public function on a public contract with no preconditions beyond holding the input token.

## Recommendation

The extension must gate the economically relevant actor, not the immediate pool caller. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. Acceptable since the router is a known periphery contract.
2. **Allowlisted forwarder mapping**: Add a separate `allowedForwarder` mapping. If `sender` is an allowlisted forwarder (e.g., the router), decode the real user from `extensionData` and check that address instead.

Either fix must be applied consistently to `exactInputSingle`, `exactInput`, `exactOutput`, and `exactOutputSingle` router paths.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for their permitted users.
3. Attacker (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: restrictedPool, ...})`.
4. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)`. Pool's `msg.sender` = router.
5. Pool calls `_beforeSwap(router, ...)`. Extension checks `allowedSwapper[pool][router]` → `true` → no revert.
6. Swap executes. Attacker receives output tokens from a pool they were not permitted to trade on.