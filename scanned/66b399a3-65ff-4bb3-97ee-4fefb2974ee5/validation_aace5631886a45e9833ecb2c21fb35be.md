### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool records `msg.sender` (the router) as `sender` and forwards it to the extension. The extension therefore checks whether the **router** is allowlisted, not the actual user. If the router is allowlisted — a natural admin action to let legitimate users reach the pool through the official periphery — every unprivileged user can bypass the curated allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) calls the pool, `sender` equals the router's address: [4](#0-3) 

The pool admin who wants allowlisted users to reach the pool through the official router must add the router to the allowlist (`setAllowedToSwap(pool, router, true)`). Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for every caller, so any unprivileged user can swap by going through the router. The extension never sees the original `msg.sender` of the router call.

The same flaw applies to every multi-hop path (`exactInput`, `exactOutput`, and the recursive `_exactOutputIterateCallback`), all of which call `pool.swap()` with `msg.sender = router`. [5](#0-4) [6](#0-5) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd, institutional, or otherwise curated addresses is fully bypassed. Any unprivileged user can execute swaps at the oracle-derived bid/ask price, draining LP liquidity or extracting value that the pool admin intended to reserve for allowlisted counterparties. This is a direct loss of LP principal and a complete failure of the curated-pool invariant.

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router — a routine and expected action whenever the pool is meant to be accessible through the official periphery. The admin has no way to simultaneously (a) allow legitimate allowlisted users to use the router and (b) block non-allowlisted users from doing the same, because the extension cannot distinguish between the two. The precondition is therefore met in every realistic deployment of a curated pool that also supports router-based access.

### Recommendation

The pool should forward the original user's address rather than `msg.sender` as the `sender` to extensions, or the router should pass the original caller explicitly. One concrete approach: add a `sender` parameter to `pool.swap()` that the router populates with `msg.sender` (the actual user), and have the pool validate that `msg.sender` is either the declared sender or a factory-registered trusted forwarder. Alternatively, `SwapAllowlistExtension` can be redesigned to gate the `recipient` or require an EIP-712 signed proof of identity from the actual user embedded in `extensionData`.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin: setAllowedToSwap(pool, alice, true)       // alice is the only allowed swapper
  admin: setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use periphery

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})

  router calls:
    pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
    // msg.sender = router

  pool calls:
    extension.beforeSwap(sender=router, ...)
    // checks allowedSwapper[pool][router] == true  ✓
    // bob's swap proceeds — allowlist fully bypassed

Result:
  bob executes a swap on a curated pool he is not authorized to trade on.
  LP funds are consumed at oracle price by an unprivileged actor.
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
