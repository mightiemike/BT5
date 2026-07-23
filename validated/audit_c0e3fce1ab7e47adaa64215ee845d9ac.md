Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Any User to Bypass the Swap Allowlist - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. If the router is allowlisted — which is required for any allowlisted user to use the router — every non-allowlisted user can bypass the guard by routing through the router.

## Finding Description
**Root cause:** `MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is the router address. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` without forwarding the original caller's identity:

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

**Structural trap:** For allowlisted users to use the router, the pool admin must set `allowedSwapper[pool][router] = true`. Once the router is allowlisted, the extension's check passes for *any* caller routing through the router, because the extension only sees the router address as `sender` — not the original user. There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

**Contrast with `DepositAllowlistExtension`:** The deposit extension correctly checks `owner` (the economically relevant actor), not `sender` (the caller). The swap extension checks `sender`, which is the wrong actor when an intermediary router is involved.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd institutions, whitelisted market makers) can be bypassed by any user routing through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against restricted LP positions, defeating the curation policy. This is a direct admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses a configured access guard, allowing unauthorized actors to trade against LP funds that were only meant to be accessible to specific counterparties.

## Likelihood Explanation
- `SwapAllowlistExtension` is a production extension in the periphery, designed for real deployment.
- `MetricOmmSimpleRouter` is the primary user-facing swap interface.
- Any pool admin who deploys a swap-allowlisted pool and allowlists the router (a natural operational step to allow their users to use the standard router) creates the bypass condition.
- The attacker needs no special privileges — only the ability to call `MetricOmmSimpleRouter`.

## Recommendation
The extension must gate on the original user, not the intermediary. Options:

1. **Preferred:** Modify `MetricOmmSimpleRouter` to encode the original `msg.sender` in `extensionData`, and update `SwapAllowlistExtension.beforeSwap` to decode and check that field when present.
2. **Alternative:** Require the router to pass the original caller as a verified field in a standardized `extensionData` format, and have the extension verify it.
3. **Simplest:** Document that the router cannot be used with swap-allowlisted pools, and add a revert in the extension if `sender` is a known router address.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is allowlisted)
  - allowedSwapper[pool][router] = true  (router allowlisted so alice can use it)
  - bob is NOT allowlisted

Direct swap by bob:
  bob → pool.swap(...)
  pool calls _beforeSwap(msg.sender=bob, ...)
  extension sees sender = bob → NOT in allowlist → REVERT ✓

Router swap by bob (bypass):
  bob → router.exactInputSingle({pool: pool, ...})
  router → pool.swap(...)
  pool calls _beforeSwap(msg.sender=router, ...)
  extension sees sender = router → IN allowlist → PASSES ✗

Result: bob executes a swap against restricted LP positions.
```