### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Complete Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the EOA. Any pool admin who allowlists the router so that their curated users can reach the pool through the standard periphery simultaneously opens the pool to every non-allowlisted user, completely defeating the allowlist.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The result is a forced dilemma for any pool admin who deploys a `SwapAllowlistExtension`:

- **Do not allowlist the router** → every allowlisted user who tries to swap through the standard periphery is blocked, because the extension sees `sender = router` and the router is not in the allowlist.
- **Allowlist the router** → every non-allowlisted user can bypass the restriction by calling `exactInputSingle` (or any other router entry point), because the extension sees `sender = router` and the router is allowlisted.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd, institutional, or otherwise curated counterparties is completely open to any address the moment the pool admin allowlists the router. The attacker needs only to call `MetricOmmSimpleRouter.exactInputSingle` with the target pool; the extension will pass because `allowedSwapper[pool][router] == true`. All swap-side curation is lost, and any user can trade against the pool's liquidity at oracle-derived prices, extracting value from LP positions that were provisioned under the assumption of a restricted counterparty set.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface documented and deployed alongside the protocol. A pool admin who wants their allowlisted users to be able to trade through the standard periphery must allowlist the router — there is no other supported path. The bypass therefore becomes active as soon as the pool is configured for real use, requiring no special timing, no privileged access, and no unusual token behavior. Any EOA can trigger it with a single public call.

### Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor, not the immediate caller of the pool. Two complementary fixes:

1. **Core**: Add an explicit `swapper` parameter to `IMetricOmmPoolActions.swap` (separate from `msg.sender`) so the router can forward the originating EOA. The pool passes this value as `sender` to `_beforeSwap`. This mirrors how `addLiquidity` already separates `msg.sender` (the adder/payer) from `owner` (the position beneficiary).

2. **Extension-only workaround** (if the core interface cannot change): `SwapAllowlistExtension.beforeSwap` should check the `recipient` argument when `sender` is a known router, or the extension should maintain a separate router→originator mapping populated by a trusted forwarding call. Neither is as clean as fixing the core interface.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the curated user
  allowedSwapper[pool][router] = true  // admin adds router so alice can use periphery

Attack (bob, not allowlisted):
  bob calls router.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient=bob, ...)
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  → swap executes for bob despite bob not being in the allowlist

Result:
  bob trades against the curated pool's liquidity at oracle prices.
  The allowlist provides zero protection once the router is added.
```

The `DepositAllowlistExtension` does **not** share this flaw: its `beforeAddLiquidity` ignores the `sender` argument entirely and gates only on `owner`, which the pool passes correctly regardless of whether the call originates from the `LiquidityAdder` or a direct EOA call. [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
