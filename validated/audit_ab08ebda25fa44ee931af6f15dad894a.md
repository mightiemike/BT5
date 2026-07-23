Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's own `msg.sender` — the router contract — not the originating user. When a pool admin allowlists `MetricOmmSimpleRouter` so that legitimate users can trade through it, every unprivileged address can bypass the curated-pool gate by routing through the same contract, completely neutralizing the allowlist.

## Finding Description

**Step 1 — Pool forwards its own `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, `_beforeSwap` is called with `msg.sender` as the first argument: [1](#0-0) 

When the caller is `MetricOmmSimpleRouter`, `msg.sender` is the router's address.

**Step 2 — `SwapAllowlistExtension` checks that forwarded `sender`.** [2](#0-1) 

`msg.sender` inside the extension is the pool; `sender` is whatever the pool forwarded — the router's address, not the originating user.

**Step 3 — The router never surfaces the originating user to the pool or extension.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly. The originating user's address is stored only in transient callback context via `_setNextCallbackContext` for payment settlement and is never passed to the pool or any extension: [3](#0-2) 

**Consequence — allowlist keyed on router, not user.**

The extension evaluates `allowedSwapper[pool][router]`. The admin faces an impossible choice:

| Admin action | Result |
|---|---|
| Do **not** allowlist the router | No user can swap through the router on this pool |
| Allowlist the router | **Every** address can swap, including non-allowlisted users |

There is no configuration that allows only specific users to trade through the router.

## Impact Explanation

Any non-allowlisted user can trade on a curated pool by routing through `MetricOmmSimpleRouter` whenever the pool admin has allowlisted the router. The swap allowlist — the sole access-control mechanism for curated pools — is fully neutralized. This constitutes a broken core pool functionality and admin-boundary break: the pool admin's ability to restrict swappers (e.g., KYC gate, institutional-only pool, regulatory restriction) is completely bypassed by an unprivileged actor using a standard, publicly available router.

## Likelihood Explanation

Allowlisting the router is the natural and expected action for any pool admin who wants users to benefit from multi-hop routing, slippage protection, and deadline enforcement. The admin has no reason to suspect that doing so opens the pool to all addresses. The trigger is a routine, semi-trusted configuration step. Any unprivileged address can exploit this as long as the router remains allowlisted, and the attack is trivially repeatable with no special capability required.

## Recommendation

The extension must check the originating user, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. Requires coordinated changes in the router and extension.
2. **Redesign the hook signature**: Pass both `sender` (the immediate caller) and `origin` (the transaction originator or a user-supplied identity verified by the router) so extensions can gate on the correct actor.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  admin allowlists userA: allowedSwapper[pool][userA] = true
  admin allowlists router (to let userA use it): allowedSwapper[pool][router] = true

Attack:
  userB (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: userB, ...})

Execution:
  router → pool.swap(userB, ...)          // pool.msg.sender = router
  pool → _beforeSwap(router, ...)         // sender = router
  extension checks allowedSwapper[pool][router] → true ✓
  swap executes; userB receives tokens from the curated pool

Result:
  userB bypasses the allowlist entirely.
  Any address can repeat this as long as the router remains allowlisted.
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
