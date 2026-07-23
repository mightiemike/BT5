### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

When a swap is routed through `MetricOmmSimpleRouter`, the `sender` argument delivered to `SwapAllowlistExtension.beforeSwap` is the **router contract address**, not the originating user. Because the allowlist is keyed on this `sender`, any user can bypass a curated pool's swap gate simply by routing through the public router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` to the pool is the **router**, so `sender` delivered to the extension is the router address. The allowlist check therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

This creates two mutually exclusive failure modes:

1. **Pool admin allowlists the router** (the only way to let any user swap through the router): every user, including those not individually allowlisted, can bypass the gate by routing through `MetricOmmSimpleRouter`.
2. **Pool admin does not allowlist the router**: individually allowlisted users cannot swap through the router at all, breaking the supported periphery path.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner explicitly passed by the caller), not on `sender`: [5](#0-4) 

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a known set of addresses provides **no effective restriction** for router-mediated swaps. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` against the pool and trade freely, because the extension sees the router address (which must be allowlisted for the periphery to function) rather than the user's address. This is a direct, complete bypass of the pool's access-control policy with no additional preconditions beyond using the public router.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint documented for end users. A pool admin who configures `SwapAllowlistExtension` and also wants users to access the pool through the router must allowlist the router, which immediately opens the gate to all users. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a standard router call.

### Recommendation

The extension must receive and gate on the **originating user**, not the intermediary. Two approaches:

1. **Pass the payer through `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before calling the pool. The extension decodes and checks that address. This requires a convention between the router and the extension.
2. **Add a `recipient`-or-payer field to the swap hook signature**: Extend `IMetricOmmExtensions.beforeSwap` with an explicit `payer` or `originator` argument that the pool populates from a trusted transient-storage slot set by the router (analogous to how the router already stores the payer for callback settlement via `TransientCallbackPool`). [6](#0-5) 

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured on `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is meant to trade.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — required so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, recipient, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully trades on a pool he was never meant to access.

The allowlist check that should have blocked Bob at step 7 instead passes because the router — not Bob — is the `sender` the extension sees. [7](#0-6)

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

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L29-32)
```text
  function _setNextCallbackContext(address pool, uint8 callbackMode, address payer, address tokenToPay) internal {
    _requireFactoryPool(pool);
    TransientCallbackPool.set(pool, callbackMode, payer, tokenToPay);
  }
```
