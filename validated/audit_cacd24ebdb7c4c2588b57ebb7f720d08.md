Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter against the per-pool allowlist. When users interact through `MetricOmmSimpleRouter`, `sender` is the router's address — not the end user's — because `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to the extension. If the router is allowlisted (required for any router-based swap), every user can bypass the allowlist entirely, rendering the access control ineffective.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

That value propagates to the extension as the `sender` parameter. `SwapAllowlistExtension.beforeSwap` then checks: [2](#0-1) 

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()` — the router, not the end user. The allowlist entry consulted is therefore `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no forwarding of the original caller: [3](#0-2) 

The actual user address is stored only in transient callback context (`_setNextCallbackContext`) for payment purposes and is never surfaced to the pool or the extension as the initiating identity. The same pattern applies to `exactOutputSingle` and multi-hop paths (`exactInput`, `exactOutput`).

The allowlist admin API names the gated entity "swapper": [4](#0-3) 

This confirms the design intent is per-user gating, not per-router gating. The check `allowedSwapper[pool][router]` is the wrong value being evaluated.

## Impact Explanation
A pool admin who deploys a `SwapAllowlistExtension` to restrict swaps to a curated set of addresses must also allowlist `MetricOmmSimpleRouter` to permit any router-based swap. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every call originating from the router, regardless of who the actual end user is. The allowlist is rendered completely ineffective: any address can swap by routing through the router. This constitutes a broken core pool functionality causing unauthorized swap access — an admin-boundary break where an unprivileged trader bypasses a pool admin's configured access control.

## Likelihood Explanation
High. The router is the primary and expected interface for end users. Pool admins who configure a swap allowlist will inevitably allowlist the router to allow normal operation, unknowingly granting universal swap access. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — only a standard router call. It is repeatable indefinitely.

## Recommendation
The `sender` parameter passed to `beforeSwap` must represent the true economic initiator of the swap, not the intermediary contract. The cleanest fix is to require the router to encode the original `msg.sender` into `extensionData`, and have the extension decode and check it. This requires a documented encoding convention enforced by the router. Alternatively, move per-user gating into the router, which does have access to the original `msg.sender`, and remove the extension-level check.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps (necessary for normal operation).
3. Non-allowlisted user Bob calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
4. Router calls `pool.swap(recipient=bob, ...)` — pool sees `msg.sender = router`.
5. Pool calls `_beforeSwap(sender=router, recipient=bob, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. Bob's swap executes successfully despite never being added to the allowlist.

Bob can repeat this for any swap direction, any amount, indefinitely. The allowlist provides zero protection against router-mediated access.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
