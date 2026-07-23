Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Guard — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which resolves to `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. Any pool admin who allowlists the router (required for router-mediated swaps to function) inadvertently grants every caller of the public router the ability to bypass the per-user allowlist.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that value against the per-pool allowlist mapping, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router itself `msg.sender` of that call: [3](#0-2) 

The pool therefore passes `sender = address(router)` to the extension. The allowlist check becomes `allowedSwapper[pool][router]`. For any user to swap through the router on an allowlisted pool, the admin must add the router to the allowlist. Once the router is allowlisted, the check is satisfied for every caller of the router — including addresses the admin never intended to permit — because the router is a public, permissionless contract.

The `extensionData` field is user-controlled and forwarded to the extension, but the extension's `beforeSwap` signature leaves the `bytes calldata` parameter unnamed and never reads it, so there is no in-band mechanism to pass the real user identity: [4](#0-3) 

## Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC-verified addresses, institutional partners, or whitelisted market makers) loses that protection entirely once the router is allowlisted. Any address can call `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` and trade against the pool. This constitutes broken core pool functionality — the allowlist guard, which is the extension's sole purpose, is rendered completely ineffective. Unauthorized traders gain access to oracle-priced liquidity the pool was not designed to offer them, which can constitute direct LP exposure to unintended counterparties and regulatory/operational harm to pool operators.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard periphery entry point for end users. Any pool admin who wants users to interact via the router must allowlist it. The bypass is therefore reachable in every realistic production deployment of `SwapAllowlistExtension` that supports router-mediated swaps. No special privileges, flash loans, or unusual token behavior are required — a single public call to the router suffices.

## Recommendation

The extension must gate the actual end user, not the direct caller of `pool.swap()`. The most robust fix is to require the router to embed the real user in `extensionData` and have the extension decode and verify it, combined with a trusted-router registry so the extension only accepts identity claims from known routers. Alternatively, enforce allowlisting at the router level with a dedicated allowlist-aware router variant, so the pool-level extension only allowlists the router and the router enforces per-user access before calling the pool.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended user
  pool admin calls setAllowedToSwap(pool, router, true)      // required for router-mediated swaps

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        ...
        extensionData: ""
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient, ..., extensionData)   // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (check passes)
        → swap executes, bob receives output tokens

  Result: bob bypasses the allowlist and trades against the pool.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
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
