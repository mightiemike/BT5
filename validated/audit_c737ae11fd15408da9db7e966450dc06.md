Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End-User, Allowing Full Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension` is intended to restrict swaps on curated pools to approved addresses. However, `beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()` — the immediate caller. When swaps are routed through `MetricOmmSimpleRouter`, the router becomes that immediate caller. If the router is allowlisted (the natural production configuration), every user who routes through it bypasses the allowlist entirely, regardless of individual approval status.

## Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the `sender` argument:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // whoever called pool.swap()
  recipient, ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the router address when routing through `MetricOmmSimpleRouter`. When a user calls `exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
  params.recipient, params.zeroForOne,
  MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
  priceLimitX64, "", params.extensionData
);
``` [4](#0-3) 

The router is `msg.sender` to the pool, so `sender` in the extension equals the **router address**, not the actual end-user. If the pool admin allowlists the router so that approved users can trade conveniently through it, the allowlist check passes for **every user** who routes through it. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` directly with the router as `msg.sender`. [5](#0-4) 

## Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners, or whitelisted market makers). The admin allowlists the router so that approved users can trade conveniently through it. Any non-approved user can then call `MetricOmmSimpleRouter.exactInputSingle()` targeting that pool and trade freely, completely defeating the curation policy. The pool's LP assets are exposed to unrestricted swap flow, and any value-extraction or front-running protection the allowlist was meant to provide is nullified. This constitutes broken core pool access control causing direct policy-level loss of fund protection for LPs.

## Likelihood Explanation

The trigger requires only that the pool admin has allowlisted the router — a natural and expected configuration for any production pool that wants to support router-mediated swaps for its approved users. The router is a public, permissionless contract. No privileged access, special tokens, or malicious setup is required. Any user who knows the pool address can call `exactInputSingle` and bypass the allowlist immediately. The condition is both realistic and likely in any production deployment.

## Recommendation

The `beforeSwap` hook must gate the **economic actor** — the end-user — not the immediate caller. Options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` and the extension decodes and checks it. This requires a coordinated convention between router and extension.
2. **Preferred fix**: The pool's `swap()` interface should expose the originating user as a distinct parameter (separate from `msg.sender`), and the extension should check that field. Alternatively, the router itself should enforce the allowlist before calling the pool, and the router should not be allowlisted at the pool level.

## Proof of Concept

```solidity
// Setup: pool admin deploys pool with SwapAllowlistExtension
// Admin allowlists the router (so approved users can trade via router)
extension.setAllowedToSwap(address(pool), address(router), true);

// Approved user: alice (allowlisted individually)
extension.setAllowedToSwap(address(pool), alice, true);

// Non-approved user: attacker (NOT allowlisted)
// Direct swap reverts:
vm.prank(attacker);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(attacker, true, 1000, type(uint128).max, "", "");

// Router-mediated swap succeeds — allowlist bypassed:
vm.prank(attacker);
router.exactInputSingle(
  IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp,
    extensionData: ""
  })
);
// Swap executes: sender seen by extension = router (allowlisted), not attacker
```

Root cause: `SwapAllowlistExtension.beforeSwap` checks `sender` (the router, `msg.sender` of `pool.swap()`) rather than the originating user, and the router is the natural entity to be allowlisted in production.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
