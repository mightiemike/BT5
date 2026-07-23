### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of the pool's `swap` is the **router contract**, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. A pool admin who allowlists the router to let their curated users trade through it inadvertently opens the gate to every user on-chain.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly — making the router the `msg.sender` the pool sees: [4](#0-3) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all recursive hops through `_exactOutputIterateCallback`): [5](#0-4) [6](#0-5) 

The pool admin has exactly two choices, both broken:

1. **Do not allowlist the router** → allowlisted users cannot use the router at all; every router-mediated swap reverts with `NotAllowedToSwap`.
2. **Allowlist the router** → the check becomes `allowedSwapper[pool][router] == true`, which passes for every caller regardless of their individual allowlist status. Any user can bypass the curated gate by routing through `MetricOmmSimpleRouter`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position beneficiary), which the `MetricOmmPoolLiquidityAdder` preserves as the caller-supplied owner address — so the deposit path does not share this flaw: [7](#0-6) [8](#0-7) 

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely the moment the pool admin allowlists the router (the natural operational step to let their own users trade). Any unprivileged address can then call `exactInputSingle` or `exactInput` on the router and execute swaps against the pool, draining LP-owned liquidity at oracle-derived prices. This is a direct loss of LP principal and a complete failure of the pool's core access-control invariant.

### Likelihood Explanation

Medium. The pool admin must take the affirmative step of allowlisting the router. However, this is the expected operational action for any pool that wants its allowlisted users to access the standard periphery. The bypass is therefore reachable on any production pool that follows the natural deployment pattern.

### Recommendation

The extension must receive and check the **original user identity**, not the intermediary. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should pass the originating user's address through the `extensionData` field (or a dedicated parameter) so extensions can decode and verify it.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode the real swapper from `extensionData` when `sender` is a known router, or the pool interface should expose a dedicated `originator` field that the router populates and the pool forwards to extensions.

The deposit allowlist's pattern of checking the economically meaningful actor (`owner`) rather than the intermediary (`sender`) should be mirrored in the swap allowlist.

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Pool admin allowlists the router so their users can trade:
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// Attacker (not individually allowlisted) calls the router directly:
router.exactInputSingle(ExactInputSingleParams({
    pool:            pool,
    recipient:       attacker,
    zeroForOne:      true,
    amountIn:        largeAmount,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    tokenIn:         token0,
    extensionData:   "",
    deadline:        block.timestamp
}));
// beforeSwap checks allowedSwapper[pool][router] == true → passes.
// Attacker receives token1 from the curated pool without being individually allowlisted.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
