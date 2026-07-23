Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Real User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for their users inadvertently grants every caller of the router unrestricted swap access, completely nullifying the allowlist.

## Finding Description

`MetricOmmPool.swap` captures `msg.sender` and passes it verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension caller) and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [3](#0-2) 

The same applies to `exactInput` (all hops): [4](#0-3) 

And to `exactOutputSingle` and the recursive `_exactOutputIterateCallback` hops: [5](#0-4) 

In every router-mediated path, the extension receives `sender = router_address`. A pool admin who wants to support router-mediated swaps for their allowlisted users must call `setAllowedToSwap(pool, router, true)`. Once the router is in `allowedSwapper[pool]`, the check `allowedSwapper[pool][router] == true` passes for **every** caller of the router, regardless of whether that caller is individually allowlisted. The `allowAllSwappers` short-circuit path is a separate flag and does not mitigate this; the `allowedSwapper` path is the broken one. [6](#0-5) 

## Impact Explanation

Any unpermissioned user can bypass the swap allowlist on a restricted pool by calling any `MetricOmmSimpleRouter` entry point. If the pool was configured to restrict swaps to KYC'd addresses, market makers, or protocol-controlled accounts, the restriction is completely nullified. Unauthorized swaps execute against LP assets at oracle-derived prices, constituting a direct loss of LP principal. This matches the allowed impact: broken core pool functionality causing loss of funds and an admin-boundary break bypassed by an unprivileged path.

## Likelihood Explanation

The router is the primary user-facing swap interface. A pool admin deploying a `SwapAllowlistExtension`-gated pool who wants to support router-mediated swaps for their allowlisted users will naturally add the router to `allowedSwapper`. This is the expected operational pattern. Once done, the allowlist provides zero protection against any EOA calling through the router. No special privilege is required — any EOA can call `MetricOmmSimpleRouter`.

## Recommendation

The extension must gate the **end user**, not the immediate caller of `pool.swap()`. The most robust fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode and check it when the immediate `sender` is a known router. Alternatively, deploy a router wrapper that enforces the allowlist before calling the pool and configure the pool to only accept swaps from that wrapper. Checking `recipient` instead of `sender` is insufficient for multi-hop paths where intermediate recipients are the router itself.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // to enable router UX
  pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
    pool: restrictedPool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: X,
    ...
  })

Trace:
  router.exactInputSingle()
    → pool.swap(recipient=attacker, ...) [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, LP assets transferred to attacker

Result:
  attacker swaps against the restricted pool despite not being individually allowlisted.
  allowedSwapper[pool][attacker] was never set to true.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
