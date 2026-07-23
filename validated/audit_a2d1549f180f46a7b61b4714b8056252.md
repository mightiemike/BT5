Audit Report

## Title
Router-mediated swap bypasses per-user `SwapAllowlistExtension` guard — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the originating EOA. Any user individually blocked by the pool admin can bypass the block by calling the public router, provided the router itself is allowlisted.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()`, the pool sees `msg.sender = router`: [4](#0-3) 

The originating EOA is never surfaced to the extension. If the pool admin allowlists the router (`allowedSwapper[pool][router] = true`) to support router-mediated swaps, the allowlist check passes for every caller of the router, including users who are individually blocked (`allowedSwapper[pool][attacker] = false`).

## Impact Explanation
The `SwapAllowlistExtension` is the sole mechanism for per-user swap gating on a pool. A pool admin who allowlists the router while blocking specific users (e.g., for compliance, sanctions screening, or protocol-level access control) has no effective enforcement: any blocked user can route through the public `MetricOmmSimpleRouter` and swap freely. The invariant "only allowlisted addresses may swap" is broken for all router-mediated paths. This constitutes broken core pool functionality causing loss of access control guarantees, meeting the allowed impact gate for broken core pool functionality.

## Likelihood Explanation
The scenario requires the pool admin to have allowlisted the router as a swapper. This is the natural and expected configuration for any pool that wants to support the official periphery router while also restricting direct swappers. The bypass requires no special privileges — any EOA can call `MetricOmmSimpleRouter.exactInputSingle`. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` entry points, all of which call `pool.swap()` with `msg.sender = router`.

## Recommendation
The extension must gate the originating user, not the immediate `pool.swap()` caller. The cleanest fix: `MetricOmmSimpleRouter` should encode `msg.sender` into `extensionData` for each hop, and `SwapAllowlistExtension` should decode and check that value when present. Alternatively, do not allowlist the router address itself; instead require that all users who wish to swap via the router are individually allowlisted — but this requires the extension to be aware of the router, which is architecturally fragile.

## Proof of Concept
```solidity
// Pool configured with SwapAllowlistExtension
// Admin allowlists router: allowedSwapper[pool][router] = true
// Admin blocks attacker: allowedSwapper[pool][attacker] = false

// Attacker calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    ...
}));
// pool.swap() is called with msg.sender = router
// beforeSwap checks allowedSwapper[pool][router] = true → passes
// Attacker swaps successfully despite being individually blocked
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
