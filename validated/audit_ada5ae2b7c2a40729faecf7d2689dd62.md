Audit Report

## Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool, which is always `msg.sender` of `pool.swap()` — the router address when a user routes through `MetricOmmSimpleRouter`. If the router is allowlisted (required for any legitimate routed swap), every non-allowlisted address can bypass the curated pool's access control by calling through the router. The allowlist is structurally misapplied: it checks the intermediary, not the economic actor.

## Finding Description
**Root cause — pool passes its own `msg.sender` as `sender`:**

In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value verbatim as `sender` to every configured extension: [2](#0-1) 

**The extension checks that `sender` argument:**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [3](#0-2) 

**The router calls `pool.swap()` directly, making itself the pool's `msg.sender`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` without forwarding the originating user's address: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Resulting invariant break:** The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. If the router is allowlisted (necessary for any legitimate routed swap), the allowlist passes for every caller of the router regardless of whether that caller is allowlisted. If the router is not allowlisted, every allowlisted user is blocked from using the router. Either configuration breaks the intended access control.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted addresses loses that restriction entirely for any user calling through `MetricOmmSimpleRouter`. The attacker receives pool output tokens and the pool receives input tokens — a direct, fund-impacting bypass of configured access control. This is an admin-boundary break: the pool admin's gating mechanism is rendered ineffective by an unprivileged path through the canonical periphery router.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical, publicly deployed periphery contract callable by any address without permission. No special setup, privileged role, flash loan, or non-standard token is required. The bypass is reachable on every swap on every allowlisted pool that also has the router allowlisted, which is the only operationally viable configuration for pools intending to support routed swaps.

## Recommendation
The extension must gate the economic actor, not the intermediary. The cleanest fix is to have the router encode `msg.sender` into `extensionData`, and have the extension decode and check that address when the caller is a known, factory-verified router. Alternatively, pools using `SwapAllowlistExtension` must document that the router cannot be allowlisted and users must call `pool.swap()` directly — but this is operationally fragile and breaks the intended UX. A factory-verified router registry combined with `extensionData`-based originator forwarding is the most robust solution.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for legitimate use
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({
        pool: allowlistedPool,
        recipient: attacker,
        zeroForOne: true,
        amountIn: X,
        ...
    })

Trace:
  router.exactInputSingle()                          // msg.sender = attacker
    → pool.swap(recipient=attacker, ...)             // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  // no revert
      → swap executes, attacker receives output tokens

Result:
  attacker bypasses the allowlist because the router is allowlisted,
  not because the attacker is allowlisted.
  The wrong value: allowedSwapper[pool][router] is checked instead of
  allowedSwapper[pool][attacker].
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
