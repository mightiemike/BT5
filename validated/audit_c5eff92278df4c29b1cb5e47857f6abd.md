Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`, so when `MetricOmmSimpleRouter` calls `pool.swap()`, the extension sees the router's address — not the end user's. If the router is allowlisted (required for any router-mediated swap to succeed), every unprivileged user can bypass the per-pool allowlist by routing through the public, permissionless router.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the per-pool allowlist mapping, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` the pool sees: [3](#0-2) 

The same pattern applies to `exactInput` (the router calls `pool.swap()` in a loop, always as `msg.sender` to the pool): [4](#0-3) 

And to `exactOutputSingle` and `exactOutput`: [5](#0-4) [6](#0-5) 

This creates an inescapable dilemma: allowlisting the router grants every user on the internet a pass through the extension; not allowlisting the router breaks router-mediated swaps for all allowlisted users. The `DepositAllowlistExtension` avoids this by checking `owner` (the position owner explicitly supplied by the caller) rather than `sender` (the direct caller): [7](#0-6) 

No equivalent identity-forwarding mechanism exists on the swap path.

## Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict swaps to KYC'd or otherwise allowlisted counterparties is fully defeated the moment the router is allowlisted. Any unprivileged user calls any router entry point, the extension sees the router's address, passes the check, and the swap executes. Unauthorized swaps against a restricted pool can drain LP principal and protocol fees — a direct loss of user funds meeting Sherlock critical/high thresholds.

## Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. No special role, token balance, or prior interaction is required. Any user who observes that the router is allowlisted on a restricted pool can exploit this in a single transaction. Likelihood is **High**.

## Recommendation

The extension must gate the actual end-user identity, not the intermediary. Viable approaches:

1. **Forwarded-sender via `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData`. The extension decodes and checks that address. Pool admins allowlist end users, not the router.
2. **Recipient-based check**: Gate on `recipient` instead of `sender` when the pool's intent is to restrict who receives output tokens — `recipient` is already available in the `beforeSwap` signature.
3. **Document incompatibility**: Explicitly document that pools using `SwapAllowlistExtension` must only be accessed via direct `pool.swap()` calls and that the router is incompatible with this extension.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, router, true)   // must allowlist router for router swaps
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ..., extensionData: ""})
  - Router calls pool.swap(recipient, ...) — msg.sender to pool = router
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true → PASSES
  - Swap executes; attacker receives output tokens

Result:
  - Attacker bypassed the allowlist entirely
  - allowedSwapper[pool][attacker] was never set to true
  - The guard checked the router, not the attacker
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L165-181)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
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
