Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates on Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the address that called `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `sender` is the router contract — not the end user. If the pool admin allowlists the router so that legitimate users can reach the pool through it, every address on the network can bypass the allowlist gate by calling the same public router, rendering the allowlist completely ineffective for router-mediated swaps.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

The real end-user (`msg.sender` at the router entry point) is stored only in transient storage via `_setNextCallbackContext` for callback payment purposes and is never forwarded to the pool as the swap initiator: [5](#0-4) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap()` directly with the router as `msg.sender`. The pool admin has no way to selectively allow legitimate users to use the router without simultaneously opening the gate to all users.

## Impact Explanation
A pool admin who deploys a pool with `SwapAllowlistExtension` intends to restrict swapping to a curated set of addresses. To allow those users to reach the pool through `MetricOmmSimpleRouter`, the admin must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, every address on the network can bypass the gate by calling any of the router's entry points. The allowlist invariant — the sole purpose of the extension — is broken for all router-mediated swaps. This constitutes a broken core pool functionality (the extension's access control is defeated) and an admin-boundary break (an unprivileged user bypasses a pool admin-configured restriction).

## Likelihood Explanation
The router is a public, permissionless contract. Any user who discovers that the router is allowlisted on a restricted pool can immediately exploit the bypass with no special privileges, no flash loan, and no complex setup. The bypass is reachable whenever the pool is intended to be usable through the router at all, which is the primary user-facing swap path.

## Recommendation
The extension must gate on the end user, not the immediate caller of `pool.swap()`. Two complementary approaches:

1. **Pass the real initiator through the pool.** Add an `initiator` field to `extensionData` that the router populates with `msg.sender` before calling the pool. The extension then checks `initiator` instead of `sender`. This requires a pool-level or router-level convention and the extension to trust the router as a gating intermediary.

2. **Check `sender` and `recipient` together.** For router-mediated swaps the recipient is typically the end user. The extension could require that either `sender` or `recipient` is allowlisted, closing the gap for the common case where the user is also the recipient.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only intended swapper
  allowedSwapper[pool][router] = true         // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

Execution path:
  router.exactInputSingle()
    → _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, bob, tokenIn)  // bob stored in transient storage only
    → pool.swap(recipient=bob, ...)           // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, bob receives tokens

Result:
  bob swaps successfully despite never being allowlisted.
  The allowlist invariant is broken for all router-mediated swaps.
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
