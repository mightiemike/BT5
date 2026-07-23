Audit Report

## Title
SwapAllowlistExtension Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as the `sender` argument to `_beforeSwap`. When `MetricOmmSimpleRouter` is the caller, `sender` equals the router address, not the end user. `SwapAllowlistExtension.beforeSwap` checks only whether that `sender` is allowlisted, so allowlisting the router — the only way to let allowlisted users trade through it — simultaneously grants every unpermissioned user a free bypass of the guard.

## Finding Description

`MetricOmmPool.swap` invokes `_beforeSwap` with `msg.sender` as the first argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← always the immediate pool caller
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then gates on that value:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the pool's `msg.sender` the router contract:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
```

The original `msg.sender` is stored only in transient storage for the payment callback (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)` at L71) and is never forwarded to the pool as the swap `sender`. The same pattern applies to `exactInput` (L103-112), `exactOutputSingle` (L135-137), and `exactOutput` (L165-181).

The pool admin faces an inescapable dilemma: not allowlisting the router blocks allowlisted users from using it; allowlisting the router lets every non-allowlisted user bypass the guard by routing through the same public contract.

## Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict trading to a curated set (KYC'd addresses, whitelisted market makers, protocol-owned accounts) is fully bypassed. Any unpermissioned address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against LP assets at oracle-derived prices. LP principal is exposed to counterparties the pool admin explicitly intended to exclude. This constitutes broken core pool functionality (the allowlist guard) with direct exposure of LP funds to unrestricted counterparties.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical production swap entry point. Any pool admin who wants allowlisted users to trade through the standard router must allowlist the router address — this is the natural and expected production configuration. Once the router is allowlisted, the bypass is reachable by any unpermissioned user with no special privileges, no setup cost, and no preconditions beyond having tokens to swap.

## Recommendation

The `sender` forwarded to extension hooks must represent the economically relevant actor — the end user — not the intermediate router. The cleanest fix is to add a `sender` override parameter to `IMetricOmmPoolActions.swap` that trusted periphery contracts can populate, with the pool verifying the caller is a factory-registered router before accepting the override. Alternatively, the router can forward `msg.sender` explicitly and the pool can pass that value as `sender` to extensions after validating the caller is trusted.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin: setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin: setAllowedToSwap(pool, router, true)  // required for alice to use router
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: curated_pool, ...})
  2. Router calls pool.swap(bob, true, X, ...) — pool's msg.sender = router
  3. Pool calls _beforeSwap(router, bob, ...)
  4. SwapAllowlistExtension.beforeSwap receives sender = router
  5. allowedSwapper[pool][router] == true → check passes
  6. bob's swap executes against LP assets

Result: bob, a non-allowlisted user, trades on a curated pool,
        bypassing the allowlist guard entirely.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only `alice` and the router, call `exactInputSingle` from `bob`, assert the swap succeeds and `bob` receives output tokens. [1](#0-0) [2](#0-1) [3](#0-2)

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
