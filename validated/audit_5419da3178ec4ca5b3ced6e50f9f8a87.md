Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of the pool's `swap` call, so the extension evaluates the router's allowlist status rather than the actual end user's. Any pool admin who allowlists the router to support router-based trading inadvertently grants swap access to every address that calls through the router, regardless of their individual allowlist status.

## Finding Description
In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

When a user calls through the router, the extension evaluates `allowedSwapper[pool][router]` — the router's allowlist status — rather than the actual user's. If the admin has allowlisted the router (the natural operational setup for router-based trading), every address that calls through the router passes the check unconditionally.

`DepositAllowlistExtension` is not affected because it checks the explicit `owner` parameter rather than `sender`, correctly binding to the economic actor regardless of who calls `addLiquidity`: [5](#0-4) 

## Impact Explanation
A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to KYC'd or whitelisted counterparties. The admin allowlists the router so that allowlisted users can trade via the standard periphery path. Once the router is allowlisted, any address — including explicitly non-allowlisted users — can call `MetricOmmSimpleRouter` and have their swap pass the extension check, because the extension sees `sender = router` (allowlisted) rather than the actual caller (not allowlisted). The curation policy is entirely defeated: non-permitted parties trade on what the admin believed was a restricted pool. This constitutes a broken core pool functionality and an admin-boundary break reachable by any unprivileged caller.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the documented production entry point for swaps. Pool admins who configure `SwapAllowlistExtension` and also want router-based trading to work for their allowlisted users will naturally allowlist the router address — this is the expected operational pattern, and it is the exact configuration that opens the bypass. No special permissions, flash loans, or exotic token behavior are required; a single public router call suffices. The precondition (router allowlisted) is the normal, expected admin action, making exploitation trivially repeatable.

## Recommendation
The extension must check the actual end user, not the intermediary:

1. **Router-side fix**: `MetricOmmSimpleRouter` should forward the real caller's address via `extensionData` so the extension can recover it. Alternatively, the pool's `swap` interface could accept an explicit `swapper` address distinct from `msg.sender`.
2. **Extension-side documentation/guard**: Until the pool interface is updated, `SwapAllowlistExtension` must document that `sender` is the direct pool caller, and pool admins must not allowlist shared routers. A long-term fix is for the extension to read the actual user from `extensionData` when a router is involved.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][router] = true   (admin enables router-based trading)
  - allowedSwapper[pool][alice] = true    (alice is a KYC'd user)
  - allowedSwapper[pool][bob]   = false   (bob is NOT allowlisted)

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
    → router calls pool.swap(recipient=bob, ...)          // router is msg.sender
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
    → swap proceeds ✓  (bob bypassed the allowlist)

Expected:
  SwapAllowlistExtension should check allowedSwapper[pool][bob] == false → revert
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
