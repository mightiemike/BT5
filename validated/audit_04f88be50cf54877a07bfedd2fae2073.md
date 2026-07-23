Audit Report

## Title
SwapAllowlistExtension checks router address instead of original user, allowing full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool, which is the pool's `msg.sender`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the **router contract address**, not the original user. If the pool admin allowlists the router to enable router-mediated swaps, every user — including those explicitly excluded from the per-user allowlist — can bypass the guard by routing through the router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value and forwards it verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap`, the pool's `msg.sender` is the **router**, not the original user: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`. The original user's identity is permanently lost at the pool boundary. The same issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

This creates an inescapable dilemma for the pool admin:

| Admin choice | Consequence |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — broken core swap flow |
| **Allowlist the router** | Every user, including explicitly blocked ones, bypasses the per-user gate |

There is no configuration of `SwapAllowlistExtension` that simultaneously allows router-mediated swaps and enforces per-user restrictions.

## Impact Explanation

On a curated pool (e.g., institutional-only, KYC-gated, or partner-restricted), LP providers deposit funds under the assumption that only allowlisted counterparties will trade against them. If the router is allowlisted to enable normal UX, any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's liquidity. This exposes LPs to adverse selection from counterparties they explicitly excluded, directly eroding LP principal. The pool's curation guarantee — its core security property — is silently voided. This constitutes a direct loss of LP principal and broken core pool functionality, meeting Critical/High impact thresholds.

## Likelihood Explanation

The router is the primary user-facing entry point for swaps. Any pool admin who configures a `SwapAllowlistExtension` and also wants users to access the pool through the router (the standard periphery path) will naturally allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a standard `exactInputSingle` call from any address.

## Recommendation

The `SwapAllowlistExtension` must gate the **original user**, not the intermediate router. Two viable approaches:

1. **Extension-data forwarding**: The router encodes `msg.sender` (the original user) into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires a documented convention between the router and the extension.
2. **Separate `originalSender` field**: Add an `originalSender` parameter to the `beforeSwap` interface that the pool populates from a trusted transient-storage slot set by the router, analogous to how the router already uses `_setNextCallbackContext` for payer tracking. [5](#0-4) 

Until fixed, pool admins must be warned that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and that allowlisting the router voids per-user restrictions entirely.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls extension.setAllowedToSwap(pool, router, true)   // to enable router UX
  admin does NOT call extension.setAllowedToSwap(pool, attacker, true)

Attack:
  attacker calls router.exactInputSingle({
      pool:       pool,
      recipient:  attacker,
      zeroForOne: true,
      amountIn:   X,
      ...
  })

Execution trace:
  router.exactInputSingle
    → pool.swap(recipient=attacker, ...)          // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  // passes — attacker never checked
      → swap executes, attacker receives output tokens
```

The attacker successfully trades on a pool from which they were explicitly excluded, with no privileged access required.

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
