Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every unprivileged caller, because the extension cannot distinguish the router from the user behind it.

## Finding Description

**Root cause — wrong identity checked**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `sender` (the direct pool caller, i.e., the router) against the per-pool allowlist — `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

**The bypass path**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making `msg.sender = router` from the pool's perspective: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput` — every router entry point calls `pool.swap` with `msg.sender = router`: [5](#0-4) 

**The natural misconfiguration**

`setAllowedToSwap` accepts any address, including the router: [6](#0-5) 

Once the router is allowlisted, `allowedSwapper[pool][router] == true` passes for every caller of the router, regardless of who that caller is. The actual user identity is never inspected.

**Contrast with DepositAllowlistExtension**

`DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position owner) rather than `sender` (the adder contract), so the deposit path does not share this flaw: [7](#0-6) 

## Impact Explanation

Any unprivileged user can bypass the swap allowlist on a restricted pool by calling `MetricOmmSimpleRouter` instead of `pool.swap` directly. The pool's LPs are exposed to swaps from counterparties the admin explicitly intended to exclude. Because the pool executes swaps at oracle-derived bid/ask prices, an unauthorized swapper can drain one side of the pool's liquidity at the same price as an authorized counterparty, causing direct LP principal loss. This meets the "direct loss of user principal" threshold.

## Likelihood Explanation

The likelihood is medium. The trigger requires the pool admin to allowlist the router, which is the natural and expected step when deploying a restricted pool that should still be accessible through the standard periphery. The admin has no on-chain signal that allowlisting the router opens the gate to all callers. `MetricOmmSimpleRouter` is a public, permissionless contract, so once the router is allowlisted the bypass is trivially reachable by any address with no special capability.

## Recommendation

The extension must gate the economic actor, not the intermediary. Two sound approaches:

1. **Require the router to forward the user address in `extensionData`** and have `SwapAllowlistExtension` decode and check that address when `sender` is a known router. This requires a protocol-level convention for the `extensionData` envelope.
2. **Remove router allowlisting as a supported pattern** and document that allowlisted users must call `pool.swap` directly. Add a `require(sender == tx.origin || allowedSwapper[pool][sender])` guard or equivalent to make router-mediated swaps always fail on allowlisted pools unless the user is also individually allowlisted.

## Proof of Concept

```
Setup
─────
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended counterparty
3. Admin calls setAllowedToSwap(pool, router, true)      // natural step: let alice use the router

Attack
──────
4. bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:       restrictedPool,
           recipient:  bob,
           zeroForOne: true,
           amountIn:   X,
           ...
       })

5. Router calls pool.swap(...) with msg.sender = router.

6. pool._beforeSwap(sender=router, ...) → SwapAllowlistExtension.beforeSwap(sender=router, ...)
       allowedSwapper[pool][router] == true  →  no revert

7. Swap executes. bob receives tokens from the restricted pool.
   The allowlist has been fully bypassed.
```

The corrupted value is `sender` passed to the extension: it is the router's address rather than `bob`'s address, so the allowlist lookup returns `true` for an actor the admin never intended to authorize.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-20)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
