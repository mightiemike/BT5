Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps on `sender`, which is the `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router becomes `sender`, not the end user. A pool admin who allowlists the router to enable router-based swaps for legitimate users inadvertently opens the gate to every unprivileged address, completely defeating the per-user swap restriction.

## Finding Description
**Step 1 — Pool passes `msg.sender` as `sender` to the extension dispatcher.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` encodes that `sender` and forwards it to every configured extension.** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`.**

`msg.sender` here is the pool (correct); `sender` is whoever called `pool.swap()` — the router, not the end user: [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call.** [4](#0-3) 

The same pattern holds for `exactInput` (L103–112), `exactOutputSingle` (L135–137), and `exactOutput` (L165–181). [5](#0-4) 

**Contrast with `DepositAllowlistExtension`:** The deposit guard correctly checks `owner` (the position owner), not `sender` (the payer), because the pool passes both separately. The swap path has no equivalent second identity field — only `sender` (= `msg.sender` of `swap`) is available to the extension. [6](#0-5) 

## Impact Explanation
When a pool admin allowlists the router to enable allowlisted users to swap via `MetricOmmSimpleRouter` (the canonical periphery path), `allowedSwapper[pool][router] = true`. Any unprivileged address can then call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and the extension evaluates `allowedSwapper[pool][router] == true`, allowing the swap to proceed. The per-user swap restriction is completely defeated, exposing LP funds to unauthorized counterparties and constituting broken core pool functionality with direct LP exposure.

## Likelihood Explanation
The `MetricOmmSimpleRouter` is the canonical swap entry point shipped with the protocol. A pool admin who deploys `SwapAllowlistExtension` and wants allowlisted users to use the router will naturally call `setAllowedToSwap(pool, router, true)` — a single, reasonable operational step. The mistake is non-obvious because the admin sees "router is allowed" and expects only router users to benefit, not realizing the router is a shared public contract that any address can call. The precondition (router allowlisted) is the natural operational choice, making exploitation trivially repeatable by any address.

## Recommendation
The extension must gate the economic actor (the end user), not the transport layer (the router). Two viable approaches:

1. **`extensionData` identity forwarding:** The router encodes the originating user's address into `extensionData`; the extension decodes and checks it. This requires the router to commit the user identity and the extension to trust the pool's faithful forwarding of `extensionData` (already done).

2. **Separate `swapper` parameter in the hook interface:** Extend `IMetricOmmExtensions.beforeSwap` with an explicit `swapper` field distinct from `sender`, populated by the pool from a router-supplied argument (e.g., a dedicated field in `callbackData` or a new swap parameter).

Until fixed, pools that need per-user swap gating must not use `SwapAllowlistExtension` together with `MetricOmmSimpleRouter`.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)      // alice is the intended swapper
  pool admin calls setAllowedToSwap(pool, router, true)     // to let alice use the router

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
      pool:      pool,
      recipient: bob,
      ...
    })

  Router executes:
    pool.swap(bob, ...)   // msg.sender = router

  Pool calls:
    _beforeSwap(router, ...)

  Extension evaluates:
    allowedSwapper[pool][router] == true  ✓  → swap proceeds

  Result: bob swaps successfully against the curated pool
          despite never being added to the allowlist.
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
