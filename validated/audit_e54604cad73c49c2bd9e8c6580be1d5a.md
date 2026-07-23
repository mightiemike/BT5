Audit Report

## Title
SwapAllowlistExtension gates on the direct pool caller (router) instead of the end user, allowing full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, that direct caller is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for permitted users inadvertently grants every unpermissioned user the ability to bypass the allowlist with a single router call.

## Finding Description

`MetricOmmPool.swap` captures `msg.sender` and passes it as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged into every configured extension via `_callExtensionsInOrder`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is the value forwarded above: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the router contract — the end user's address is never surfaced to the pool: [4](#0-3) 

The same mismatch applies to `exactInput` (multi-hop forward), `exactOutputSingle`, and `exactOutput` (recursive callback path): [5](#0-4) 

The extension therefore checks `allowedSwapper[pool][router]`. The actual end-user address is never consulted. There is no secondary check, no `tx.origin` fallback, and no mechanism for the router to authenticate the originating user to the extension.

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to trusted counterparties (e.g., professional market makers) and also wants those counterparties to use `MetricOmmSimpleRouter` must call `setAllowedToSwap(pool, router, true)`. Once that entry exists, every user who calls the router passes the allowlist check, because the extension only sees the router's address. The intended access boundary is completely nullified. Unauthorized users can trade at oracle-derived prices in a pool designed to exclude them, extracting value from LPs who deposited under the assumption that only trusted counterparties would trade. This constitutes a direct loss of LP assets and a broken admin-boundary invariant.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entry point. Any pool admin who deploys a curated pool with `SwapAllowlistExtension` and also wants to support router-mediated swaps for their allowlisted users will naturally allowlist the router. The bypass is then reachable by any unprivileged user with a single router call. No special permissions, flash loans, or unusual token behavior are required. The precondition (router allowlisted) is the natural and expected operational state for any pool that intends to support both access control and router usage.

## Recommendation

The `beforeSwap` hook must gate on the economically relevant actor — the address that initiated the transaction and will pay for the swap — rather than the direct caller of `pool.swap()`. The preferred fix is to have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` using a well-defined convention, and have `SwapAllowlistExtension.beforeSwap` decode and verify that field when `sender` is a known router. Alternatively, redesign the hook signature so the pool passes both the direct caller (`sender`) and an authenticated originator field that the router populates, and have `SwapAllowlistExtension` gate on the originator. Using `tx.origin` is fragile and should be avoided.

## Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
   — intending to allow allowlisted users to reach the pool via the router.
3. attacker (not in the allowlist) calls:
     router.exactInputSingle(ExactInputSingleParams{
       pool: pool,
       recipient: attacker,
       zeroForOne: true,
       amountIn: X,
       ...
     })
4. router calls pool.swap(attacker, true, X, ...).
   pool sets sender = address(router).
   Extension checks allowedSwapper[pool][router] → true → swap proceeds.
5. attacker successfully swaps in a pool that was supposed to block them,
   receiving tokens at oracle-derived prices and extracting value from LPs.
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
