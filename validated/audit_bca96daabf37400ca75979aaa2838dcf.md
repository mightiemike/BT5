Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of real user, allowing full allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which is `msg.sender` of the pool's `swap` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. A pool admin who allowlists the router to support router-mediated swaps for approved users inadvertently grants unrestricted swap access to every caller of the public router, completely defeating the per-user allowlist guard.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool: [3](#0-2) 

The router stores the real user in transient storage via `_setNextCallbackContext(..., msg.sender, ...)` for payment purposes only — it is never forwarded to the extension. The extension has no mechanism to recover the actual end-user identity. A pool admin faces an impossible choice: if the router is not allowlisted, all router-mediated swaps revert even for individually approved users; if the router is allowlisted, every caller of the permissionless public router can swap on the restricted pool regardless of their individual allowlist status.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties) can be fully bypassed by any unprivileged user calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The allowlist guard silently passes for all router callers, constituting a broken core pool access-control mechanism. Unauthorized users can execute swaps the pool admin explicitly intended to prohibit, directly impacting pool liquidity and constituting a broken core pool functionality with direct fund impact.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap interface. Any pool admin who wants to support router-mediated swaps for their allowlisted users will naturally add the router to the allowlist — this is the expected configuration step. Once done, the bypass is reachable by any address with no special privileges, no setup, and no preconditions beyond calling the public router. The attack is repeatable and requires zero on-chain footprint beyond a normal swap call.

## Recommendation
The extension must gate on the actual end-user, not the intermediary. Two complementary approaches:

1. **Forward the real user through the router**: `MetricOmmSimpleRouter` should encode `msg.sender` (the real user) into `extensionData` before calling `pool.swap`. `SwapAllowlistExtension.beforeSwap` should decode and check that value when `sender` is a known router address.

2. **Router registry in the extension**: Maintain a registry of trusted routers. When `sender` is a registered router, require and verify the real user identity supplied in `extensionData`; when `sender` is not a router, check `sender` directly as today.

The simplest safe fix is option 1: `MetricOmmSimpleRouter` prepends `abi.encode(msg.sender)` to `extensionData`, and `SwapAllowlistExtension` decodes and checks the real user when `sender` matches a registered router.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only approved user
  allowedSwapper[pool][router] = true         // admin adds router to support alice's router swaps

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
      → pool.swap(recipient, ...) with msg.sender = router
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  → PASSES
        → swap executes for bob

Result:
  bob swaps on a pool he was explicitly barred from accessing.
  The allowlist guard is completely bypassed.
```

Foundry test outline:
1. Deploy pool with `SwapAllowlistExtension` attached.
2. Call `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)`.
3. From `bob` (not allowlisted), call `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. Assert the swap succeeds — confirming the bypass.
5. Confirm that calling `pool.swap(...)` directly from `bob` reverts with `NotAllowedToSwap`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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
