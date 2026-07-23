Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the pool's `msg.sender`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address rather than the end user's address. If the pool admin allowlists the router (required for router-mediated swaps to function), every user — including those not individually allowlisted — can bypass the swap gate by routing through the router.

## Finding Description
In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

When `MetricOmmSimpleRouter.exactInputSingle` is the caller, it invokes `pool.swap(params.recipient, ...)` directly, making the router the pool's `msg.sender`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router — not the end user: [3](#0-2) 

The router stores the actual end user (`msg.sender`) only in transient storage for the payment callback via `_setNextCallbackContext`, and never exposes it to the extension layer: [4](#0-3) 

Because the router's address is a single shared value, allowlisting it grants every user — regardless of individual allowlist status — the ability to trade on the curated pool. The existing `allowAllSwappers` bypass check does not help here; the issue is that `allowedSwapper[pool][router] = true` is semantically equivalent to `allowAllSwappers[pool] = true` for all router-mediated swaps.

## Impact Explanation
A pool admin who deploys a curated pool (e.g., KYC-only, institutional-only) with `SwapAllowlistExtension` and allowlists the router to support normal UX inadvertently opens the pool to all users. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` targeting the restricted pool, and the extension will approve the swap because it sees the allowlisted router address, not the user's address. LP funds are directly at risk from counterparties the pool was designed to exclude. This constitutes a broken core pool access-control mechanism causing direct loss exposure to LP positions provisioned under the assumption of a restricted counterparty set.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard periphery entry point for swaps. Any pool admin who wants allowlisted users to use the router must allowlist the router address — this is the natural and expected operational step. The bypass is triggered by normal, non-adversarial pool configuration. An attacker needs only to call the public router with the restricted pool address — no special privileges, no flash loans, no multi-step setup. The precondition (router allowlisted) is a near-certainty for any production deployment.

## Recommendation
The extension must gate on the economically relevant actor, not the immediate caller of `pool.swap`. The cleanest fix is to have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` under a known prefix, and have `SwapAllowlistExtension.beforeSwap` decode and check it, falling back to `sender` for direct pool calls. Alternatively, deploy a router wrapper that enforces the allowlist before calling the pool, and configure the pool to only accept calls from that trusted wrapper.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is KYC'd
  allowedSwapper[pool][router] = true  // router allowlisted so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
      pool: restrictedPool,
      tokenIn: token0,
      tokenOut: token1,
      ...
    })

  pool.swap(msg.sender=router) fires _beforeSwap(sender=router)
  SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  Bob's swap executes successfully despite not being on the allowlist.

Direct call check (for comparison):
  bob calls pool.swap() directly
  _beforeSwap(sender=bob)
  allowedSwapper[pool][bob] → false → revert NotAllowedToSwap ✓

Conclusion: the allowlist is enforced for direct calls but silently bypassed
for all router-mediated calls once the router is allowlisted.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
