Audit Report

## Title
`SwapAllowlistExtension#beforeSwap()` Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension` is designed to gate pool swaps by swapper address, but it receives `msg.sender` from the pool's perspective as the `sender` argument. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to support normal UX, every non-allowlisted user can bypass the gate by calling any router `exact*` function targeting the curated pool.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension via `abi.encodeCall`:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the original caller's identity:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

The pool therefore sees `msg.sender = router`, and the extension evaluates `allowedSwapper[pool][router]` — never `allowedSwapper[pool][actual_user]`. The actual user's identity is invisible to the extension. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

## Impact Explanation
A pool admin who deploys a curated pool (KYC-gated, institutional, or otherwise restricted) and allowlists the router to support standard swap UX loses all access control. Any unprivileged user calls `router.exactInputSingle()` targeting the curated pool; the extension sees `sender = router`, finds it allowlisted, and permits the swap. LP principal in the curated pool is exposed to unrestricted trading by unintended counterparties, directly violating the pool's intended invariant. This constitutes broken core pool functionality (allowlist bypass) with direct fund-impacting consequences — LP value leakage and bad-price execution by unintended counterparties.

## Likelihood Explanation
The router is the standard, documented user-facing entry point for swaps. Any pool admin who wants allowlisted users to have a normal swap UX must allowlist the router, which immediately triggers the bypass. The attacker requires no special privilege — only the ability to call a public router function. The condition is trivially reachable and repeatable by any EOA.

## Recommendation
The extension must gate the economically relevant actor, not the pool's direct caller. Viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. Requires a coordinated convention between router and extension.
2. **Use `tx.origin` as a fallback identity**: When `sender` is a known router (or any non-EOA), fall back to `tx.origin`. Safe in this context because the extension is only checking identity for allowlist purposes, not for payment authorization.
3. **Redesign the pool's `sender` binding**: Have the pool accept an explicit `swapper` parameter distinct from `msg.sender`, which the router populates with its own `msg.sender` before calling `pool.swap()`.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted for UX
4. bob (non-allowlisted EOA) calls:
       router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap()
   → pool passes msg.sender=router as `sender` to _beforeSwap
   → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
   → bob's swap succeeds despite not being allowlisted ✗
5. bob calls pool.swap() directly → sender=bob → correctly blocked ✓
   bob calls router.exactInputSingle() → sender=router → incorrectly allowed ✗
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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
