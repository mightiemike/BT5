Audit Report

## Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded from the pool, which equals `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` is the caller, `sender` is always the router address. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users inadvertently grants every router caller — including non-allowlisted addresses — unrestricted swap access, completely neutralising the extension's access gate.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to every configured extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [2](#0-1) 

In `MetricOmmSimpleRouter.exactInputSingle`, the real user's address is stored only in transient callback context via `_setNextCallbackContext` for payment settlement. The call to `pool.swap()` is made directly by the router, so the pool sees `msg.sender = router`: [3](#0-2) 

The real user address is never passed as a swap argument and never reaches the extension. The same flaw exists in `exactInput` (multi-hop): [4](#0-3) 

And in the `exactOutput` recursive callback path: [5](#0-4) 

Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[pool][sender]` evaluates to `true` for every caller of the router regardless of who initiated the transaction, because `sender` is always the router address. The allowlist is completely neutralised for all router-mediated swaps.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (KYC'd users, institutional LPs, whitelisted market makers) becomes fully open to any address routing through `MetricOmmSimpleRouter`. Unauthorized traders can execute swaps against the pool's LP positions. The pool's core invariant — "only allowlisted addresses may swap" — is broken for the primary public entry point, constituting broken core pool functionality and exposing LP principal to unauthorized counterparties in an access-controlled pool.

## Likelihood Explanation
The trigger requires the pool admin to allowlist the router address. This is a structurally induced, natural action: allowlisted users will attempt to use the router (the standard UX path), find their swaps reverting because the router is not allowlisted, and the admin will add the router to unblock them. The admin has no mechanism to say "allow user X through the router" — the only available granularity is the router address itself, which grants access to all router callers. The mistake is induced by the design with no warning or alternative.

## Recommendation
The extension must check the originating user, not the intermediary contract. Two viable approaches:

1. **Pass the real caller through `extensionData`:** The router encodes `msg.sender` into `extensionData` for each hop; the extension decodes and checks it. This requires a coordinated convention between router and extension.
2. **Add a `realSender` field to the pool's swap interface:** The pool accepts an explicit `realSender` argument (verified against `msg.sender` or a trusted router registry) and forwards it to extensions instead of `msg.sender`.

Until fixed, pool admins must be warned that allowlisting the router address is equivalent to disabling the allowlist for all router users.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  admin calls extension.setAllowedToSwap(pool, alice, true)
  admin calls extension.setAllowedToSwap(pool, router, true)   // to unblock router UX

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: T0, tokenOut: T1, ...})

  router calls:
    pool.swap(recipient=bob, ...)   // msg.sender at pool = router

  pool calls:
    _beforeSwap(sender=router, ...)

  extension checks:
    allowedSwapper[pool][router] == true  → passes

  Result: bob's swap executes against the pool's LP positions.
          The allowlist never checked bob's address.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
