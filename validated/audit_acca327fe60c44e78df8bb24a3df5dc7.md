Audit Report

## Title
SwapAllowlistExtension checks router address instead of originating user, allowing full allowlist bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which is bound to `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is always the router's address. A pool admin who allowlists the router — the only way to permit any router-mediated swap on a restricted pool — inadvertently grants every user access, because the extension cannot distinguish individual callers once the router is the immediate caller of `pool.swap()`.

## Finding Description

**Hook argument binding**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every registered extension: [2](#0-1) 

**The allowlist check**

`SwapAllowlistExtension.beforeSwap` receives `sender` (the immediate caller of `pool.swap()`) and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool: [3](#0-2) 

**Router path — exactInput**

`MetricOmmSimpleRouter.exactInput` calls `pool.swap()` directly in a loop, making the router the `msg.sender` of every hop: [4](#0-3) 

**Router path — exactOutput intermediate hops**

For `exactOutput`, intermediate hops are executed inside `_exactOutputIterateCallback`, where the router is again the direct caller of `pool.swap()`: [5](#0-4) 

**The bypass**

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. Any pool admin who wants legitimate users to use the router on a restricted pool must call `setAllowedToSwap(pool, router, true)`. Once that entry is set, every caller — including those explicitly excluded — can invoke any router entry point and the extension will pass them through.

**Contrast with DepositAllowlistExtension**

The deposit allowlist correctly gates on `owner` (the LP position owner, a separate parameter that `MetricOmmPoolLiquidityAdder` sets to the actual user), not on `sender`: [6](#0-5) 

The swap allowlist has no equivalent "actual user" parameter; it only has `sender`, which collapses to the router for all router-mediated swaps.

## Impact Explanation

This is an admin-boundary break: the per-user swap allowlist (an admin-configured access control boundary) is fully bypassed by an unprivileged path (the public router). Any pool using `SwapAllowlistExtension` to enforce KYC, institutional access, or regulatory restrictions is rendered open to all callers once the router is allowlisted. The wrong value is the extension's boolean decision — it returns `beforeSwap.selector` (pass) when it should revert with `NotAllowedToSwap`. This maps directly to the allowed impact category of admin-boundary break and broken core pool functionality causing unauthorized swap execution.

## Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router. This is a necessary precondition for any legitimate user to use the router on a restricted pool — the admin has no mechanism to selectively allow some users through the router while blocking others, because the extension sees only the router address. Any pool that combines `SwapAllowlistExtension` with router support is affected. The admin action is semi-trusted, but the resulting bypass is fully unprivileged and repeatable by any address.

## Recommendation

The extension must identify the originating user, not the immediate caller. Two viable approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode the originating `msg.sender` into `extensionData` for each hop. `SwapAllowlistExtension.beforeSwap` decodes it when `sender` is a known/trusted router address, and falls back to `sender` otherwise. This preserves the router's role as a trusted intermediary while restoring per-user granularity.

2. **Recipient-based check**: For single-hop swaps (`exactInputSingle`, `exactOutputSingle`), gate on `recipient` instead of `sender` (the user sets `recipient` to themselves). Require the router to pass the user as `recipient` for multi-hop swaps. This is a weaker fix as it conflates recipient with authorized swapper.

Approach (1) is the cleanest long-term fix.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, alice, true).
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so alice can use the router).

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({tokenIn: token0, ...}).
  - Router calls pool.swap(recipient=bob, ...) with msg.sender = router.
  - Extension receives sender = router.
  - allowedSwapper[pool][router] == true → extension passes.
  - Bob's swap executes successfully despite not being on the allowlist.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
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
