Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which resolves to `msg.sender` inside `MetricOmmPool.swap` — the immediate caller of the pool, not the originating user. When a user routes through the public `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every non-allowlisted user can bypass the per-user allowlist by routing through the same public router.

## Finding Description

**Confirmed call chain:**

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The root cause is that `sender` in `beforeSwap` is always the direct pool caller. There is no mechanism in the extension to recover the originating user from `extensionData` or any other source. The pool admin faces an irresolvable dilemma: allowlisting the router grants access to all router callers, not just the intended allowlisted users.

## Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd users, whitelisted market makers) is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. The LP assets of the curated pool are exposed to unauthorized swaps, enabling adverse selection and direct loss of LP principal. This is a broken core pool invariant — the allowlist extension fails to gate the actor the pool admin intended to restrict.

## Likelihood Explanation

High. `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` targeting the curated pool. No special privilege or setup is required beyond knowing the pool address. The pool admin is likely to allowlist the router because without it, allowlisted users lose access to all router convenience features (multi-hop, exact-output, slippage protection).

## Recommendation

The extension must resolve the end-user identity from the router's transient callback context rather than trusting the `sender` parameter alone. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData` before calling the pool. The extension decodes and checks that address. This requires a trusted encoding convention between the router and extension.
2. **Sender + router fallback**: The extension checks `allowedSwapper[pool][sender]` first; if `sender` is a known router, it additionally checks a user-identity field the router must supply in `extensionData`.

As a minimum safe default, document and enforce in code that the router must never be allowlisted and that allowlisted users must call the pool directly.

## Proof of Concept

```solidity
// Setup: pool admin creates curated pool with SwapAllowlistExtension
// Admin allowlists alice (legitimate user) and the router (to let alice use it)
swapAllowlist.setAllowedToSwap(pool, alice, true);
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// Attack: bob (not allowlisted) bypasses the allowlist via the router
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: token0,
        recipient: bob,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// ✓ Swap succeeds: extension saw sender=router, router is allowlisted
// ✓ Bob swapped on a pool he was never authorized to access
```

The extension receives `sender = address(router)`, which is allowlisted, so `allowedSwapper[pool][router]` returns `true` and the guard passes — even though `bob` is not allowlisted. [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-176)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
