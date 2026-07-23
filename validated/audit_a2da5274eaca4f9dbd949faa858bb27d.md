Audit Report

## Title
SwapAllowlistExtension gates on router address instead of originating user, allowing allowlist bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument — which is `msg.sender` of `pool.swap()` — against the per-pool allowlist. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the originating user. Any pool admin who allowlists the router (the only way to permit router-mediated swaps on a restricted pool) inadvertently grants every user access, because the extension cannot distinguish individual callers once the router is the immediate caller of `pool.swap()`.

## Finding Description

**Hook argument binding**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every registered extension: [2](#0-1) 

**The allowlist check**

`SwapAllowlistExtension.beforeSwap` receives `sender` (the immediate caller of `pool.swap()`) and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool: [3](#0-2) 

**Router path**

`MetricOmmSimpleRouter.exactInput` calls `pool.swap()` directly in a loop, making the router the `msg.sender` of every hop: [4](#0-3) 

For intermediate hops in `exactOutput`, `_exactOutputIterateCallback` is again the direct caller of each `pool.swap()`: [5](#0-4) 

`exactInputSingle` and `exactOutputSingle` also call `pool.swap()` directly from the router: [6](#0-5) 

**The bypass**

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. A pool admin who wants any user to use the router must allowlist the router address. Once the router is allowlisted, every user — including those explicitly excluded — can call any router entry point and the extension will pass them through.

**Contrast with DepositAllowlistExtension**

The deposit allowlist correctly gates on `owner` (the LP position owner, a separate parameter that `MetricOmmPoolLiquidityAdder` sets to the actual user), demonstrating the design intent was per-user gating: [7](#0-6) 

The swap allowlist has no equivalent "actual user" parameter; it only has `sender`, which collapses to the router for all router-mediated swaps.

## Impact Explanation

Any user can bypass a per-user swap allowlist on a restricted pool by routing through `MetricOmmSimpleRouter`. Pools using the allowlist to enforce KYC, institutional access, or regulatory restrictions are fully open to any caller once the router is allowlisted. This is an admin-boundary break: the allowlist (an admin-configured access boundary) is bypassed by an unprivileged path (the public router). The exact wrong value is the extension decision — `allowedSwapper[pool][router] == true` is evaluated instead of `allowedSwapper[pool][user]`, causing the hook to return `beforeSwap.selector` (pass) when it should revert with `NotAllowedToSwap`.

## Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router. This is a necessary step for any legitimate user to use the router on a restricted pool — the admin cannot selectively allow some users to use the router while blocking others, because the extension sees only the router address. Any pool that combines `SwapAllowlistExtension` with router support is affected. The admin action is semi-trusted but the resulting bypass is fully unprivileged and repeatable by any caller.

## Recommendation

The extension must identify the originating user, not the immediate caller. Two viable approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode the originating `msg.sender` into `extensionData` for each hop. `SwapAllowlistExtension.beforeSwap` decodes it when `sender` is a known router address, and falls back to `sender` otherwise.

2. **Recipient check**: Gate on the `recipient` argument instead of `sender` for single-hop swaps (users typically set `recipient` to themselves), and require the router to pass the user as `recipient` for multi-hop swaps.

Approach (1) is the cleanest long-term fix, preserving the router's role as a trusted intermediary while restoring per-user granularity.

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
  - allowedSwapper[pool][router] == true → extension returns beforeSwap.selector.
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
