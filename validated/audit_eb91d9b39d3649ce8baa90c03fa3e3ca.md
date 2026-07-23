Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is used, the pool's `msg.sender` is the router, not the original user. A pool admin who allowlists the router to enable router-mediated swaps for approved users inadvertently grants swap access to every user of the public router, completely nullifying the per-user allowlist policy.

## Finding Description

**Step 1 — Pool passes `msg.sender` (the router) as `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

When `MetricOmmSimpleRouter` calls `pool.swap()`, `msg.sender` at the pool level is the router contract address.

**Step 2 — Extension checks `sender`, which resolves to the router address.**

`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [2](#0-1) 

**Step 3 — The router calls `pool.swap()` directly without forwarding the original user.**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` with no mechanism to pass the original `msg.sender` to the extension: [3](#0-2) 

The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

**Step 4 — The broken invariant.**

The extension is designed to gate "by swapper address, per pool." When the admin allowlists the router (to enable router-mediated swaps for their approved users), the check becomes `allowedSwapper[pool][router] == true` for every user who routes through the router — the guard fails open for all users. [5](#0-4) 

## Impact Explanation

A pool admin who configures a curated pool (e.g., KYC-gated, institutional-only) and allowlists the router to enable router-mediated swaps for their approved users inadvertently opens the pool to every user of the public router. Any unprivileged address can call `exactInputSingle` or `exactInput` on the curated pool and the `SwapAllowlistExtension` will pass because it checks the router's address (which is allowlisted), not the caller's address (which is not). This is an admin-boundary break: the pool admin's configured access control is bypassed by an unprivileged public entrypoint, meeting the contest's allowed impact gate.

## Likelihood Explanation

The trigger requires a semi-trusted admin action (allowlisting the router) combined with an unprivileged user action (calling the router). Allowlisting the router is a natural and expected configuration step for any curated pool that wants to support the standard periphery. `MetricOmmSimpleRouter` is the canonical swap entrypoint. There is no warning in the extension or the router that allowlisting the router address grants access to all router users. Likelihood is medium-high for any curated pool that enables router access.

## Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor — the original user — not the intermediary router. Options:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData` and the extension decodes and checks it. Requires a coordinated convention between router and extension.
2. **Add an `originator` field to the swap interface:** The pool passes both `msg.sender` (the direct caller) and an explicit `originator` (the economic actor) to extensions, allowing the allowlist to gate the correct identity regardless of routing path.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin allowlists alice: allowedSwapper[pool][alice] = true
  - Admin allowlists router: allowedSwapper[pool][router] = true
    (to enable alice to swap via the router)

Attack:
  - charlie (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ..., recipient: charlie})
  - Router calls pool.swap(charlie, ...) → pool sees msg.sender = router
  - Pool calls _beforeSwap(router, charlie, ...)
  - Extension checks allowedSwapper[pool][router] == true → PASSES
  - Charlie's swap executes on the curated pool despite not being allowlisted

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
