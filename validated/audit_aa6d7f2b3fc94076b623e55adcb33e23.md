Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which resolves to `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the originating user. A pool admin who allowlists the router to enable legitimate router-mediated swaps simultaneously opens the allowlist to every address, rendering the guard inoperative for all router-mediated swaps.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` is the pool (the extension's caller). `sender` is the first argument forwarded from `MetricOmmPool.swap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // direct caller of pool.swap(), not the originating user
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()`:

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

`msg.sender` of `pool.swap()` is the router, so `sender` passed to `beforeSwap` is the router address. The check becomes `allowedSwapper[pool][router]`. The originating user's address is never evaluated. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

This creates an irresolvable dilemma: if the admin does not allowlist the router, allowlisted users cannot use the router at all. If the admin allowlists the router (the natural operational step), every address — including non-allowlisted ones — can bypass the restriction by routing through the same contract. No existing guard in the extension, pool, or router prevents this.

## Impact Explanation

A pool deploying `SwapAllowlistExtension` is a curated pool restricted to specific counterparties (e.g., KYC'd users, institutional LPs). Once the router is allowlisted, any address can execute swaps in the restricted pool via `MetricOmmSimpleRouter`. The allowlist — a core pool protection — fails completely open for all router-mediated swaps. Unauthorized users can extract value from a pool whose pricing or liquidity was calibrated for a specific, trusted set of counterparties. This constitutes broken core pool functionality causing direct loss of funds to LPs and the pool.

**Severity: High**

## Likelihood Explanation

The router is the primary user-facing swap entrypoint. Any pool admin who wants allowlisted users to use the router must allowlist it — this is the expected operational path, not an edge case. The bypass is reachable in any production deployment of a curated pool that supports router access. No special attacker capability is required beyond calling the public router function.

**Likelihood: Medium**

## Recommendation

The extension must gate the economically relevant actor — the user who initiated the swap — not the intermediate contract that called `pool.swap()`. Two approaches:

1. **Extension-data forwarding**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. Requires coordinated changes to the router and extension.
2. **Dedicated `originalSender` field**: Add an `originalSender` field to the `beforeSwap` hook signature, populated by the pool from a transient-storage context set by the router before calling `pool.swap()`.

Additionally, `DepositAllowlistExtension` should be audited for the symmetric issue on the `addLiquidity` path via `MetricOmmPoolLiquidityAdder`.

## Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, userA, true)   // allowlist user A
3. Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist router so user A can use it
4. Non-allowlisted userB calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:          restrictedPool,
           tokenIn:       token0,
           recipient:     userB,
           amountIn:      X,
           extensionData: ""
       })
5. Router calls restrictedPool.swap(userB, ..., "")
6. pool.swap sets sender = address(router)
7. beforeSwap checks allowedSwapper[pool][router] → true  ✓
8. Swap executes for userB — allowlist fully bypassed.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
