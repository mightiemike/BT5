Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, so when a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. `SwapAllowlistExtension.beforeSwap` then checks whether that `sender` (the router) is allowlisted, not the actual user. If the pool admin allowlists the router to permit allowlisted users to use the standard periphery, every unprivileged user can bypass the individual allowlist by routing through the router.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [3](#0-2) 

The same pattern applies to `exactInput` (multi-hop) and `exactOutputSingle`: [4](#0-3) [5](#0-4) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The pool admin faces an impossible choice: not allowlisting the router breaks the standard periphery flow for allowlisted users; allowlisting the router neutralises the allowlist entirely for all users.

The `extensionData` field passed by the router is hardcoded to `""` (empty string) in all router call sites, so the extension cannot recover the original caller from it.

## Impact Explanation

Once the pool admin allowlists the router (the expected operational step to let allowlisted users use the periphery), any unprivileged user can call `router.exactInputSingle()` and trade on a pool intended to be restricted. The allowlist guard is completely neutralised on the router path. This directly exposes LP principal to disallowed actors on KYC-gated, partner-restricted, or front-runner-guarded pools. This matches the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality causing loss of funds" impact categories.

## Likelihood Explanation

High. The router is the canonical periphery entry point. Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and wants allowlisted users to use the router will allowlist the router. The bypass is then immediately available to every user with no special setup — only a standard `router.exactInputSingle()` call targeting the curated pool is required.

## Recommendation

The pool must surface the original user's identity to the extension. The most robust fix is for the router to encode `msg.sender` into `extensionData` and for the extension to decode and check it instead of the raw `sender` argument. Alternatively, add an explicit `originator` field to the swap interface that the pool passes to extensions, allowing the router to forward `msg.sender` without ambiguity.

## Proof of Concept

```solidity
// Pool admin sets up a curated pool with SwapAllowlistExtension.
// Only `alice` is allowlisted.
extension.setAllowedToSwap(address(pool), alice, true);

// Pool admin allowlists the router so alice can use the periphery.
extension.setAllowedToSwap(address(pool), address(router), true);

// `eve` (not allowlisted) bypasses the guard via the router:
vm.prank(eve);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: eve,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        extensionData: "",
        deadline: block.timestamp + 1
    })
);
// Succeeds: extension evaluated allowedSwapper[pool][router] == true.
// Eve traded on a pool she was explicitly excluded from.
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
