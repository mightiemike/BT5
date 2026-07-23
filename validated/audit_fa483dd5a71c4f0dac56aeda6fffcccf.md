Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender` instead of the originating user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist, but `sender` is set to `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the originating user. If the pool admin allowlists the router (the natural operational step to let allowlisted users reach the pool via the router), every non-allowlisted user gains identical access by calling the same public router, completely defeating the allowlist.

## Finding Description

**Root cause — `MetricOmmPool.swap` passes its own `msg.sender` as `sender` to the extension:**

In `MetricOmmPool.swap`, `_beforeSwap` is called with `msg.sender` as the first argument: [1](#0-0) 

**`SwapAllowlistExtension.beforeSwap` checks that value against the allowlist:**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool (correct for the mapping key) and `sender` is whoever called `pool.swap()`.

**`MetricOmmSimpleRouter` calls `pool.swap()` directly, making itself the `sender`:**

`exactInputSingle`, `exactInput`, and `exactOutputSingle` all call `IMetricOmmPoolActions(params.pool).swap(...)` with the router as `msg.sender`: [3](#0-2) [4](#0-3) [5](#0-4) 

So when any user routes through the router, the extension sees `sender = router_address`, not the original user. The allowlist check `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][user]`.

**The configuration dilemma is irresolvable:**

| Admin choice | Effect on allowlisted users | Effect on non-allowlisted users |
|---|---|---|
| Do **not** allowlist the router | Cannot use the router | Correctly blocked |
| **Allowlist the router** | Can use the router | **Also pass — bypass achieved** |

No existing guard in `SwapAllowlistExtension` or `MetricOmmSimpleRouter` captures the originating user's identity. The router stores `msg.sender` in transient storage for the payment callback (`_setNextCallbackContext`), but this value is never forwarded to the extension. [6](#0-5) 

## Impact Explanation

Any non-allowlisted user can bypass the swap allowlist on any pool that has `SwapAllowlistExtension` configured and the router allowlisted. The allowlist is the pool's primary access-control boundary for swaps. Bypassing it lets unprivileged addresses execute swaps the pool operator explicitly intended to block — a direct admin-boundary break by an unprivileged path. Pools deployed for regulated or permissioned trading (KYC, institutional-only, etc.) lose their access control entirely. This matches the allowed impact gate: **Admin-boundary break — pool admin's access-control mechanism is bypassed by an unprivileged path.**

## Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it. The only prerequisite is that the pool admin allowlists the router — a routine operational step any admin would take to let their allowlisted users access the router. The bypass is reachable by any user as soon as the pool is configured for normal router use, with no special privileges or unusual conditions required.

## Recommendation

The extension must check the **original user's identity**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the original user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.
2. **Add an `originator` field to the hook interface**: The pool passes both `sender` (immediate caller) and `originator` (the address the router recorded as the economic actor). The extension checks `originator`.

Until fixed, pools that need a swap allowlist must not allowlist the router, which means allowlisted users cannot use the router — a broken core swap flow.

## Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // so alice can use the router
4. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) — msg.sender = router
6. _beforeSwap(sender=router, ...) → SwapAllowlistExtension.beforeSwap
7. Check: allowedSwapper[pool][router] == true  → passes
8. Bob's swap executes successfully, bypassing the allowlist.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
