Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Enabling Full Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` always sets to `msg.sender` — the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. A pool admin who allowlists the router to enable standard periphery UX inadvertently grants every user unrestricted access to the pool, completely defeating the per-address allowlist.

## Finding Description
`MetricOmmPool.swap()` hardcodes `msg.sender` as the `sender` argument forwarded to every before-swap extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // always the direct caller of pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap()` then checks that `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

At this point `msg.sender` inside the pool is the **router address**, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The originating user's address is never visible to the extension. The router passes `params.extensionData` through from the caller, but `SwapAllowlistExtension.beforeSwap()` ignores the `extensionData` parameter entirely — it only checks `sender`.

The same problem applies to all other router entry points: `exactInput` (L104), `exactOutputSingle` (L136), `exactOutput` (L165), and the recursive `_exactOutputIterateCallback` (L220) — in every case the router is the direct caller of `pool.swap()`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity()` correctly gates the economically relevant actor by checking `owner` (the position beneficiary), not `sender` (the operator/payer). No equivalent originator forwarding exists on the swap path.

## Impact Explanation
A pool admin who wants to allow router-mediated swaps must call `setAllowedToSwap(pool, router, true)`. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[msg.sender][sender]` passes for **every** user who routes through the router, regardless of whether that user is individually allowlisted. The curated pool's access control is completely bypassed for all router-originated swaps. LP providers deposit into a pool expecting only vetted counterparties to trade against them; any unpermissioned user can call `exactInputSingle` through the router and trade against the pool at oracle price, extracting value from LPs who believed they were in a restricted venue. This constitutes direct loss of LP principal through unauthorized swap execution on a pool whose core invariant is restricted access.

## Likelihood Explanation
The scenario requires the pool admin to allowlist the router, which is the natural and expected configuration for any pool that wants to support the standard periphery UX. The router is a public, permissionless contract. Any user who discovers the bypass can exploit it immediately with no special privileges or setup. The trigger is a standard `exactInputSingle` call. The condition (router allowlisted) is not adversarial — it is the normal operational state for any pool using the router.

## Recommendation
The swap allowlist must gate the actual originating user, not the intermediate router. Two complementary fixes:

1. **Extension-data forwarding**: The router encodes the originating user's address into `extensionData` for each hop. `SwapAllowlistExtension.beforeSwap()` decodes and checks that address instead of (or in addition to) `sender`.

2. **Pool-level originator field**: Add an optional `originator` parameter to `pool.swap()` that the pool passes to extensions alongside `sender`. The router sets `originator = msg.sender`; direct callers leave it as `address(0)` (falling back to `sender`). The extension checks `originator != address(0) ? originator : sender`.

Either approach must be applied consistently across all router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) and the recursive callback path in `_exactOutputIterateCallback`.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router-mediated swaps
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is not allowlisted

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, ...)
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  5. Swap executes — alice trades against the curated pool despite not being allowlisted

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
