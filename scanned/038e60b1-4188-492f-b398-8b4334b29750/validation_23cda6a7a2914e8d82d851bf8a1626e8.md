### Title
SwapAllowlistExtension Allowlist Bypassed via Router: Any Unprivileged User Can Swap in Restricted Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address that called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router's address is forwarded as `sender`, not the actual user's address. If the pool admin allowlists the router so that legitimate users can use it, every unprivileged user can bypass the allowlist entirely by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed on that `sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router itself the `msg.sender` seen by the pool: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

Because the extension sees `sender = router`, the pool admin faces an impossible choice:

- **Allowlist the router** so that legitimate users can use it → every unprivileged user can bypass the allowlist by routing through the router, because the check becomes `allowedSwapper[pool][router]`, which is `true` for all callers.
- **Do not allowlist the router** → allowlisted users cannot use the router at all, breaking the expected UX.

There is no mechanism in the current design to thread the original user's address from the router through to the extension. The `SwapAllowlistExtension` is therefore structurally unable to gate on the actual economic actor when the router is in the call path.

The analog to the Augur C01 bug is exact: in Augur, `MarketFactory` is a registered `trustedSender`, so any caller can invoke `createMarket` with a malicious `_universe` and the Augur contract executes a `transferFrom` on behalf of the factory without verifying the real initiator. Here, the router is the registered "trusted sender" seen by the allowlist extension, so any caller can invoke `router.exactInput*` and the extension approves the swap without ever checking the real initiator.

### Impact Explanation

Any user not on the allowlist can swap in a pool that the admin intended to restrict. If the pool carries favorable pricing (lower spread or notional fees), the unauthorized swapper extracts value from LPs at rates the admin never intended to offer them. If the pool is a compliance-gated venue (KYC, institutional-only), the bypass defeats the entire access-control layer. This is a direct admin-boundary break: an unprivileged path (the public router) causes the configured guard to pass for actors it was explicitly meant to block.

### Likelihood Explanation

High. The router is a public, permissionless contract. Any user can call it. A pool admin who deploys `SwapAllowlistExtension` and wants legitimate users to use the router will naturally allowlist the router address, at which point the bypass is immediately available to every address on-chain. No special privileges, flash loans, or multi-step setup are required.

### Recommendation

The `SwapAllowlistExtension` must gate on the real economic actor, not the intermediary. Two viable approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` in `extensionData` and have the extension decode and check that address. The extension should reject calls where `sender` is a known router but no valid user address is provided in the payload.

2. **Check `sender` only, never the router**: Remove the router from the allowlist entirely and document that allowlisted users must call the pool directly. Add a clear NatSpec warning that allowlisting any intermediary contract defeats the guard.

The `DepositAllowlistExtension` is not affected by the same issue because it gates on `owner` (the position owner explicitly passed by the caller), not on `sender` (the intermediary). [6](#0-5) 

### Proof of Concept

```
Setup:
  pool = factory.createPool(..., extensions=[SwapAllowlistExtension], ...)
  admin.setAllowedToSwap(pool, alice, true)          // alice is KYC'd
  admin.setAllowedToSwap(pool, router, true)          // router allowlisted so alice can use it

Attack (bob is NOT allowlisted):
  bob calls router.exactInputSingle({
      pool:      pool,
      tokenIn:   token0,
      recipient: bob,
      amountIn:  X,
      ...
  })

  router calls pool.swap(bob, zeroForOne, X, ...)
    → pool calls extension.beforeSwap(router, bob, ...)
    → extension checks allowedSwapper[pool][router]  → true  (router is allowlisted)
    → swap executes; bob receives token1

Result:
  bob swapped successfully despite never being on the allowlist.
  The SwapAllowlistExtension guard was completely bypassed.
``` [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
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
