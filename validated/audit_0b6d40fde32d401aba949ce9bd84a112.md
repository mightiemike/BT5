Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` validates the `sender` argument, which `MetricOmmPool.swap()` sets to its own `msg.sender` — the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, that direct caller is the router contract, not the end user. Any pool admin who allowlists the router to support normal periphery UX inadvertently grants every unprivileged address the ability to bypass per-user swap restrictions entirely.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` (the direct caller) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards this value unchanged to every configured extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap` then checks that value against its per-pool allowlist: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the original caller's identity: [3](#0-2) 

The router stores the original `msg.sender` only in transient storage for the payment callback (`_setNextCallbackContext`), but never encodes it into `extensionData` or any argument visible to the extension. The extension therefore always sees `sender = router`, never the end user.

For any router-mediated swap to succeed on an allowlisted pool, the admin must call `setAllowedToSwap(pool, router, true)`. Once that entry exists, `allowedSwapper[pool][router] == true` satisfies the check for every caller regardless of their individual allowlist status. The existing guard is structurally insufficient: it checks the intermediary, not the initiator.

## Impact Explanation

A pool admin deploying a curated pool with `SwapAllowlistExtension` (e.g., for KYC-gated or permissioned trading) intends to restrict swaps to a specific set of addresses. The moment the router is allowlisted — a necessary step for normal UX — the allowlist provides zero protection for router-mediated swaps. Any address can call `exactInputSingle` or `exactInput` on the router pointing at the curated pool and execute a swap successfully. This constitutes a direct bypass of a core pool access-control mechanism, enabling unauthorized trading on pools designed to be restricted. This matches the "Admin-boundary break: pool admin exceeds caps, bypasses timelocks, or factory/oracle role checks are bypassed by an unprivileged path" allowed impact.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, documented swap entrypoint for end users. Pool admins who configure `SwapAllowlistExtension` and also want their users to use the router will inevitably allowlist the router address — there is no documented warning against this. The bypass requires no special privileges, no flash loans, and no multi-step setup. Any address can call `exactInputSingle` on the router pointing at the curated pool. The combination of a natural admin action and a publicly reachable entrypoint makes exploitation highly likely in practice.

## Recommendation

Two complementary fixes:

1. **Router-side**: Encode the original `msg.sender` into the `extensionData` forwarded to the pool (e.g., `abi.encode(msg.sender, params.extensionData)`), so allowlist extensions can recover and check the true initiator.
2. **Extension-side**: When `extensionData` is non-empty, decode and validate the embedded caller address instead of (or in addition to) the raw `sender` argument. Optionally, require the embedded address to be signed or attested by a trusted router to prevent spoofing.

Alternatively, document explicitly that `SwapAllowlistExtension` is incompatible with router-mediated swaps and that pool admins must never allowlist the router if they intend to enforce per-user restrictions.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for router UX
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the only intended user
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender of pool.swap() = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
  5. bob's swap executes successfully despite not being on the allowlist

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist completely bypassed
```

Foundry test: deploy pool with `SwapAllowlistExtension`, configure as above, call `exactInputSingle` from an un-allowlisted address, assert no revert occurs.

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
