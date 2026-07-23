Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual end-user, allowing any unprivileged swapper to bypass the per-user allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool call. When a swap is routed through `MetricOmmSimpleRouter`, `sender` equals the router address, not the actual end-user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the pool to every user who routes through it, completely defeating the per-user allowlist gate.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension: [2](#0-1) 

Inside `SwapAllowlistExtension.beforeSwap`, the check is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user swaps directly, `sender` = user — the check works as intended. When a user swaps through `MetricOmmSimpleRouter`, the router calls `pool.swap()` directly: [4](#0-3) 

This makes `sender` = router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. Critically, `SwapAllowlistExtension.beforeSwap` ignores the `bytes calldata` (extensionData) parameter entirely — it is unnamed and never decoded: [5](#0-4) 

The router also never encodes the real user identity into `extensionData` — it passes `params.extensionData` directly from the caller without augmentation: [6](#0-5) 

For the router to be usable at all on an allowlisted pool, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once that entry is set, `allowedSwapper[pool][router] = true` satisfies the condition for every swap arriving via the router, regardless of who the real end-user is. [7](#0-6) 

## Impact Explanation
The swap allowlist is the pool admin's primary mechanism for restricting who may trade against the pool's liquidity. Bypassing it allows unauthorized actors to execute swaps. This maps directly to the "admin-boundary break" impact class: a pool-admin-configured guard is bypassed by an unprivileged path (any user routing through the public router). In an oracle-anchored pool the per-swap LP loss is bounded by the spread, but the allowlist may exist for compliance, KYC, or toxic-flow-prevention reasons. Repeated unauthorized swaps accumulate spread-bounded losses against LP principal and violate the pool admin's intended access-control invariant.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is the standard user-facing entry point. Any pool that (a) has `SwapAllowlistExtension` configured and (b) has allowlisted the router — the only way to support router-mediated swaps — is permanently exposed. No special privilege or timing is required; any EOA can call the router's `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` functions.

## Recommendation
The extension must resolve the real end-user identity rather than the immediate caller. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` (the actual swapper) into `extensionData`; `SwapAllowlistExtension.beforeSwap` decodes and checks it when present, falling back to `sender` for direct calls. This requires a trusted encoding convention between the router and extension.
2. **Check `recipient` or require direct-only swaps**: Gate on the `recipient` argument if the pool design equates recipient with the economically relevant party, or document that allowlisted pools must not be used with the public router.

The cleanest fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData`, and the extension decodes and checks that value when `extensionData` is non-empty.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin: setAllowedToSwap(pool, router, true)   // enable router
  pool admin: setAllowedToSwap(pool, alice, true)    // alice is allowed
  // bob is NOT allowlisted

Attack:
  bob calls pool.swap(...) directly
    → sender = bob
    → allowedSwapper[pool][bob] = false → REVERT ✓ (correctly blocked)

  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
    → pool passes msg.sender (= router) as sender to _beforeSwap
    → extension checks allowedSwapper[pool][router] = true → PASS ✗ (bypass)
    → bob's swap executes against LP liquidity
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
