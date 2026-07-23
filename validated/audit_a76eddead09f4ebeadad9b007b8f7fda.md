All four code references in the claim are confirmed against the actual production code. The vulnerability is real and exploitable as described.

Audit Report

## Title
`SwapAllowlistExtension` gates on the router address instead of the real user, allowing complete allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` at the pool, so the extension checks whether the router is allowlisted rather than whether the actual user is allowlisted. Any unprivileged user can bypass a curated pool's swap allowlist by calling the standard periphery router, provided the router itself is allowlisted — which is a prerequisite for the router to be usable at all.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (used as the namespace key) and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` at the pool level: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The router stores the real payer in transient storage via `_setNextCallbackContext(..., msg.sender, ...)` but this information is never propagated to the extension hook. There is no existing guard that recovers the real initiating user before the allowlist check executes.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses provides no real restriction. The pool admin must allowlist the router address for any router-mediated swap to succeed; once the router is allowlisted, every user — including those not individually allowlisted — can trade on the curated pool simply by calling the router. This is a complete, direct bypass of the pool's access-control invariant. The wrong value is the `sender` argument checked by the extension: it is the router contract address rather than the economic initiator (the EOA or contract that called the router). This constitutes broken core pool functionality causing the access-control mechanism to be entirely ineffective.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. No privileged access, special token behavior, or unusual setup is required. Any user who calls the router bypasses the allowlist. The bypass is reachable on every swap through the router and requires no discovery of an obscure code path.

## Recommendation
The pool must propagate the real initiating user through the call stack so extensions can gate on the correct actor:

1. **Primary fix:** Add an `initiator` parameter to `pool.swap()` (or encode it in `extensionData`) and forward it to `beforeSwap` alongside `sender`. The router already holds the real payer in transient storage (`_getPayer()`); it should pass `msg.sender` as the initiator when calling `pool.swap()`. `SwapAllowlistExtension` should then gate on `initiator` rather than `sender`.

2. **Short-term mitigation:** `SwapAllowlistExtension` could read the real user from a standardized prefix in `extensionData`, though this shifts trust to the caller and is less robust.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so any router swap works at all).
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true).

Attack:
  1. Alice (not allowlisted) calls router.exactInputSingle({pool: pool, ...}).
  2. Router calls pool.swap(...) — msg.sender at pool = router address.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. ExtensionCalling forwards sender=router to SwapAllowlistExtension.beforeSwap.
  5. Extension checks allowedSwapper[pool][router] == true → passes.
  6. Alice's swap executes on the curated pool despite not being allowlisted.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
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
