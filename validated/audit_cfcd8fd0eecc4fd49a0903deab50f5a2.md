Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of Real User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap()` sets to `msg.sender` — the immediate caller of the pool. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. A pool admin who allowlists the router address to enable standard periphery access inadvertently opens the allowlist to every user, because the router's identity passes the check regardless of who called the router.

## Finding Description

The root cause is a three-step identity loss:

**Step 1:** `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap()`. [1](#0-0) 

**Step 2:** `ExtensionCalling._beforeSwap()` forwards `sender` verbatim to every configured extension via `_callExtensionsInOrder`. [2](#0-1) 

**Step 3:** `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value from step 1 — the router address, not the end user. [3](#0-2) 

**Step 4:** `MetricOmmSimpleRouter.exactInputSingle` stores the original caller only in transient callback state (`_setNextCallbackContext`) and never forwards it to the extension. The pool call receives the router as `msg.sender`. [4](#0-3) 

The allowlist is supposed to gate the economically relevant actor — the user who initiates and pays for the swap. Instead it gates the intermediary contract (the router), which is a shared, permissionless entry point. Any user who calls `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` through the router on a pool where the router is allowlisted bypasses the guard entirely.

## Impact Explanation

A curated pool's swap allowlist is the primary access-control boundary. Bypassing it allows unauthorized users to trade on pools restricted to specific counterparties (regulatory/compliance breach), and allows any user to execute swaps on pools where the allowlist was the only guard preventing bad-price or high-volume extraction by untrusted actors. This is a direct admin-boundary break: the pool admin's intent to restrict swaps is completely voided by the supported periphery path. Severity: **High**.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical swap entry point. Any pool admin who deploys a `SwapAllowlistExtension` and wants allowlisted users to access the pool through the standard router will allowlist the router address — this is the expected operational pattern. No special knowledge or adversarial setup is required. Any user who calls any router entry point on such a pool bypasses the guard. The failure is silent and automatic.

## Recommendation

The extension must check the original user's identity, not the intermediary's. The cleanest fix: the router encodes `abi.encode(msg.sender)` as a prefix in `extensionData` before calling the pool, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `msg.sender` (the pool) is the caller. This requires a convention between router and extension but preserves the allowlist invariant without breaking the existing call path.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (intending to allow standard periphery access for allowlisted users)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(...) — msg.sender at pool = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true → PASSES
  - Attacker's swap executes on the curated pool despite never being allowlisted

Wrong value: allowedSwapper[pool][router] = true is evaluated instead of
             allowedSwapper[pool][attacker], which is false.
             The extension decision (allow) is incorrect for the actual initiating user.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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
  }
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
