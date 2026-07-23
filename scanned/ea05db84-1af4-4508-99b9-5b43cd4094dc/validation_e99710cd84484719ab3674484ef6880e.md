### Title
`SwapAllowlistExtension` gates on the direct pool caller (router address) instead of the end-user, allowing per-user allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router**, not the end-user. If the pool admin allowlists the router address (a natural step to let users trade through the router), every unprivileged address can bypass the per-user allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension` is documented as "Gates `swap` by swapper address, per pool." [1](#0-0) 

Its `beforeSwap` hook receives `sender` as the first argument and checks it against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the pool calls the extension). `sender` is whatever the pool passed as the first argument to `_beforeSwap`.

In `MetricOmmPool.swap()`, the pool passes `msg.sender` — the direct caller of `pool.swap()` — as `sender`: [2](#0-1) 

```solidity
_beforeSwap(
    msg.sender,   // ← this is the router when called via MetricOmmSimpleRouter
    recipient,
    zeroForOne,
    ...
);
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly: [3](#0-2) 

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

So the call chain is:

```
end-user → router.exactInputSingle()
         → pool.swap()  [msg.sender = router]
         → _beforeSwap(sender = router, ...)
         → extension.beforeSwap(sender = router, ...)
         → allowedSwapper[pool][router]  ← checked, NOT the end-user
```

A pool admin who wants allowlisted users to be able to trade through the router must add the router to `allowedSwapper[pool][router]`. The moment they do, **every address** can call `router.exactInputSingle()` and the extension passes, because the check resolves to `allowedSwapper[pool][router] == true` regardless of who the actual end-user is.

The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all four router entry points call `pool.swap()` from the router's address.

<cite repo="Oyahkilomeikhide/2026-07-metric-

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-83)
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
```
