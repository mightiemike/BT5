Audit Report

## Title
SwapAllowlistExtension Gates Router Address Instead of Originating User, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap()` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the router contract becomes `msg.sender` of `pool.swap()`. A pool admin who allowlists the router so that legitimate users can trade via the standard periphery inadvertently opens the gate to every address, because the extension evaluates `allowedSwapper[pool][router]` — always `true` — rather than the originating user's identity.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and all other `exact*` entry points) calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`. Once the router is allowlisted, that mapping entry is `true` for every call arriving through it, regardless of who called the router. There is no existing guard that recovers the originating user's identity; the `extensionData` field is passed through but the extension does not decode it.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` parameter — the position owner explicitly supplied by the caller — not `sender`, so it is not affected by router intermediation: [5](#0-4) 

## Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely once the router is allowlisted. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the pool; the extension passes because it sees the allowlisted router address, not the blocked user. LP assets are exposed to unrestricted swaps from actors the pool was designed to exclude, causing direct loss of LP principal through adverse selection or price impact. This constitutes a broken core pool functionality causing loss of funds and an admin-boundary break where an unprivileged path bypasses a configured access control.

## Likelihood Explanation

The bypass requires the pool admin to have allowlisted the router address. This is a routine, expected action: any pool admin who wants their allowlisted users to be able to use the standard periphery router must add the router to the allowlist. The moment they do, the gate is open to everyone. The attacker needs no special privilege — only the ability to call a public router function. The condition is therefore met in every realistic deployment of a curated pool that intends to support router-mediated trading.

## Recommendation

`SwapAllowlistExtension` should gate on the originating user rather than the direct caller of `pool.swap()`. The cleanest fix is to have the router encode the originating `msg.sender` into `extensionData` before calling the pool, and have the extension decode and check that address when `extensionData` is non-empty. The pool admin then allowlists individual users, not the router, and the router-mediated path is gated correctly. Alternatively, extend the `beforeSwap` interface with an `originSender` field populated by the router.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  alice  → allowedSwapper[pool][alice]  = true   (legitimate user)
  router → allowedSwapper[pool][router] = true   (admin adds router so alice can use it)

Attack (bob, not allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(...)          // msg.sender = router
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  → swap executes for bob despite bob not being allowlisted

Result:
  bob successfully swaps on a pool restricted to alice only.
  LP assets are exposed to an unrestricted counterparty.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, allowlist only `alice` and the router, call `exactInputSingle` from `bob`, assert the swap succeeds (demonstrating the bypass) and that `bob`'s token balance changes.

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
