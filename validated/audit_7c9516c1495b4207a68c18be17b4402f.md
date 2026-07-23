Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. This produces two mutually exclusive fund-impacting failures: if the router is allowlisted, every user on the planet can bypass the curated allowlist; if the router is not allowlisted, every legitimately allowlisted user is silently blocked from the primary swap path.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first positional argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern holds for `exactInput` (all hops): [5](#0-4) 

And for `exactOutputSingle` and `exactOutput` recursive hops: [6](#0-5) 

In every router path the extension receives `sender = router_address`. The actual end-user identity is never delivered to the guard. The router stores the original `msg.sender` only in transient storage as the payer for callback settlement, but this value is never surfaced to the extension hook.

## Impact Explanation
Two mutually exclusive fund-impacting failure modes:

**Mode A — Full allowlist bypass (critical policy failure):** Pool admin allowlists the router so that allowlisted users can trade through it. Because the check is `allowedSwapper[pool][router]`, every user who calls the router — including users explicitly excluded from the allowlist — passes the gate. The curated pool's access control is completely nullified for all router-mediated swaps. This is a direct admin-boundary break by an unprivileged path.

**Mode B — Broken core swap functionality for allowlisted users:** Pool admin does not allowlist the router (only individual users). Every allowlisted user who attempts to swap through `MetricOmmSimpleRouter` is rejected with `NotAllowedToSwap`, because the router's address is not in the allowlist. The primary user-facing swap path is broken for the pool's intended participants, satisfying the broken core pool functionality impact gate.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical periphery swap entry point. Any production pool that deploys `SwapAllowlistExtension` to restrict trading to a curated set of addresses will encounter one of the two failure modes the moment a user or the admin interacts through the router. No special attacker capability is required — a normal `exactInputSingle` call is sufficient to trigger Mode A if the router is allowlisted, or Mode B if it is not.

## Recommendation
The `beforeSwap` hook should gate on the end-user identity, not the intermediary. Two complementary fixes:

1. **Pass the original caller through the router.** The router already stores the original `msg.sender` in transient storage as the payer. The extension could read it from a trusted router-provided field in `extensionData`, or the pool's `swap` interface could accept an explicit `originator` argument.

2. **Encode originator in `extensionData`.** Have the router encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value when the direct `sender` is a known router, or redesign the hook signature so the pool passes both the immediate caller and the originating EOA.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  alice  → allowedSwapper[pool][alice]  = true
  bob    → allowedSwapper[pool][bob]    = false
  router → allowedSwapper[pool][router] = true   ← admin adds this so alice can use the router

Attack (Mode A — bypass):
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=bob, ...)          // router is msg.sender
    → pool calls _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → bob's swap executes despite being explicitly excluded

Attack (Mode B — DoS of allowlisted user):
  alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=alice, ...)         // router is msg.sender
    → pool calls _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == false  ✗
    → alice's swap reverts with NotAllowedToSwap despite being allowlisted
```

Foundry test plan: deploy a pool with `SwapAllowlistExtension`, allowlist alice directly, allowlist the router, then `vm.prank(bob)` call `router.exactInputSingle(...)` and assert it succeeds (Mode A bypass). In a separate test, do not allowlist the router, `vm.prank(alice)` call `router.exactInputSingle(...)` and assert it reverts with `NotAllowedToSwap` (Mode B DoS).

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
