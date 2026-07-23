Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of originating user, enabling full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` is intended to restrict swaps on a curated pool to approved addresses. However, `beforeSwap` receives `sender` from the pool, which is the pool's own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][real_user]`. A pool admin who allowlists the router (the natural fix for allowing approved users to use the router) inadvertently grants every unprivileged user the ability to bypass the allowlist entirely.

## Finding Description

**Pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`:** [1](#0-0) 

When `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract. The pool then calls `_beforeSwap(msg.sender, ...)`, forwarding the router address as `sender`.

**Router calls the pool directly with no mechanism to forward the original caller:** [2](#0-1) 

The router stores the original `msg.sender` in transient storage for the payment callback (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`), but this value is never forwarded to the pool or encoded into `extensionData`. The pool receives no information about the originating user.

**Extension evaluates the wrong actor:** [3](#0-2) 

`allowedSwapper[msg.sender][sender]` resolves to `allowedSwapper[pool][router]`. The real user's address is never consulted. The check is structurally correct (pool as key, swapper as sub-key) but the `sender` argument is the router, not the user.

**Existing guards are insufficient:** The `allowAllSwappers[msg.sender]` short-circuit only bypasses the check entirely; it does not fix the identity confusion. There is no other mechanism in the extension or pool to recover the originating user's address.

## Impact Explanation

A pool admin deploying a curated pool with `SwapAllowlistExtension` faces an inescapable dilemma:

- **Path A – router not allowlisted**: Approved users' router calls revert because `allowedSwapper[pool][router] == false`. The canonical production entry point is broken for all allowlisted users.
- **Path B – admin allowlists the router**: `allowedSwapper[pool][router] = true`. Every unprivileged user can call `router.exactInputSingle({pool: curated_pool, ...})` and the extension passes, because it only checks whether the router is allowlisted. The allowlist is completely bypassed.

In Path B, unauthorized traders execute swaps against a pool whose LPs deposited under the assumption that only vetted counterparties would trade. This directly exposes LP principal to adversarial flow — a direct loss of user principal impact.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical, production-grade entry point. Any pool admin who wants allowlisted users to be able to use the router will naturally allowlist the router address, triggering Path B. The bypass requires no special privileges, no flash loans, and no exotic tokens — only a standard router call. Likelihood is **Medium** (requires the admin to allowlist the router, which is the natural remediation for Path A).

## Recommendation

The extension must recover the original user identity rather than relying on the `sender` argument forwarded by the pool. Two complementary fixes:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a coordinated change in the router and extension.
2. **Check `sender` at the router level before calling the pool**: The router reads the allowlist and reverts before the pool call if the caller is not approved. This keeps the extension as a last-resort guard and adds a router-level gate.

The cleanest on-chain fix is option 1, because it preserves the extension as the single source of truth and does not require the router to know about every pool's allowlist configuration.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (Alice is the intended gated user)
  - allowedSwapper[pool][router] = true  (admin adds this so Alice can use the router)

Attack:
  1. Charlie (not allowlisted) calls:
       router.exactInputSingle({pool: curated_pool, ...})
  2. Router calls pool.swap(recipient=charlie, ...)
       → pool's msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
       [MetricOmmPool.sol L230-240]
  4. Extension evaluates:
       allowedSwapper[pool][router] == true  → passes
       [SwapAllowlistExtension.sol L37]
  5. Swap executes. Charlie receives tokens from the curated pool.
     The allowlist guard was a no-op for Charlie's actual address.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
