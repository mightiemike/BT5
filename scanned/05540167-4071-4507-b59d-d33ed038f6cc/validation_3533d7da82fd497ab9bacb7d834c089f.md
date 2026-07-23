### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` — which equals the pool's `msg.sender`, i.e. the router contract — against the per-pool allowlist. When a pool admin allowlists the router so that their curated users can swap through it, every non-allowlisted user gains the same access by routing through `MetricOmmSimpleRouter`.

### Finding Description

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` value against the allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap`, so `sender` = router address: [4](#0-3) 

The allowlist check therefore resolves to `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (the natural action to let curated users trade through the standard periphery), every non-allowlisted user can bypass the gate by routing through `MetricOmmSimpleRouter`.

This is structurally inconsistent with `DepositAllowlistExtension`, which correctly gates by `owner` (the economic actor who receives the LP position), not by `sender` (the `MetricOmmPoolLiquidityAdder` contract): [5](#0-4) 

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g. KYC'd counterparties, whitelisted market makers) and then allowlists the router so those users can access the standard periphery inadvertently opens the pool to every address. Any non-allowlisted user can execute swaps against the pool's full liquidity, breaking the intended access control and exposing LP funds to trades the pool was configured to reject.

### Likelihood Explanation

Allowlisting the router is the expected operational step for any pool that uses `SwapAllowlistExtension` alongside the standard periphery. A pool admin who does not allowlist the router forces all curated users to call the pool directly, making the router unusable for that pool. The bypass condition is therefore reached by any pool that follows the natural deployment pattern.

### Recommendation

Gate the allowlist on the economically relevant actor, not the technical caller. Two options:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and the extension.
2. **Check `recipient` instead of `sender`**: For exact-input swaps the recipient is typically the user. This is imperfect for multi-hop flows but eliminates the router-address bypass.

The deposit allowlist's pattern — checking `owner`, the address that will own the resulting position — is the correct model to follow.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is curated
3. Pool admin calls setAllowedToSwap(pool, router, true)  // enable router for curated users
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
   → pool.swap(bob, ...) with msg.sender = router
   → beforeSwap(sender=router, ...)
   → allowedSwapper[pool][router] == true  → no revert
5. Bob's swap executes successfully despite not being on the allowlist.
``` [6](#0-5) [7](#0-6)

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
