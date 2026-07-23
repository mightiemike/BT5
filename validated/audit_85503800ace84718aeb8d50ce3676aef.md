Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates, `sender` is the router's address, not the end-user's. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the gate to every address on-chain, completely defeating the allowlist.

## Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` argument verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

So `msg.sender` to the pool is the router address. The extension receives `sender = router`, and evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The admin faces an impossible choice:
- **Do not allowlist the router** → allowlisted users cannot use the router.
- **Allowlist the router** → every user on-chain passes the extension check, bypassing the allowlist entirely.

## Impact Explanation

`SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may swap on a pool. A complete bypass means any address can execute swaps on a pool the operator intended to be permissioned (KYC-gated, institutional-only, whitelist-only). This constitutes a broken core pool functionality / admin-boundary break: unauthorized users can drain one-sided liquidity at oracle price, and the allowlist invariant stored in `allowedSwapper` is rendered meaningless. [6](#0-5) 

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point. Any pool deploying `SwapAllowlistExtension` and expecting users to interact via the router will encounter this issue. No special privilege is required — any unprivileged address can call the router. The only precondition is that the pool admin has allowlisted the router, which is the expected operational setup for router-mediated pools.

## Recommendation

The extension must gate the **end-user identity**, not the intermediary. Two sound approaches:

1. **Pass the original caller through `extensionData`**: The router encodes the actual user address in `extensionData`; the extension decodes and checks it, while also verifying the router address itself is trusted.
2. **Add a `swapper` parameter to `pool.swap()`**: The pool accepts an explicit `swapper` address (validated against `msg.sender` or a trusted forwarder list) and passes it as `sender` to extensions. This is the cleanest fix.

Until fixed, pools using `SwapAllowlistExtension` should not allowlist the router and should require users to call `pool.swap()` directly.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` attached; `allowAllSwappers[pool] = false`.
2. Admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is permitted.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so `userA` can use the router.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Router calls `pool.swap(userB, ...)` — `msg.sender` to the pool is the router.
6. Pool calls `_beforeSwap(msg.sender=router, ...)` → extension receives `sender=router`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. `userB`'s swap executes successfully despite never being allowlisted. [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
