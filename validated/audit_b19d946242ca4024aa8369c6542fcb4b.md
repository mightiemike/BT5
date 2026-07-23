### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the actual user. The allowlist therefore gates the router address rather than the individual swapper, making the per-user curation policy unenforceable on any router-mediated path.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap` is the **router contract address**, so the extension receives `sender = router`. The check `allowedSwapper[pool][router]` is evaluated — not `allowedSwapper[pool][actual_user]`.

This creates an irresolvable dilemma for the pool admin:

- **Router not allowlisted**: every allowlisted user who tries to swap through the router is blocked, breaking the supported periphery path.
- **Router allowlisted**: every non-allowlisted user can bypass the curation policy by routing through the public router contract.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates by the explicit `owner` parameter (the economic beneficiary), not by `sender`: [5](#0-4) 

The swap path has no equivalent "owner" parameter — the economic beneficiary is `recipient`, but the extension ignores it (second argument is unnamed/discarded) and checks `sender` instead.

### Impact Explanation

Any user who is **not** individually allowlisted on a curated pool can execute swaps on that pool by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The router is a public, permissionless contract. If the pool admin allowlists the router to support legitimate users, the allowlist is effectively nullified for all users. Direct loss of the curation guarantee; unauthorized users trade on pools that were designed to be restricted.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entry point documented in the periphery. Pool admins who deploy a `SwapAllowlistExtension` and also want their allowlisted users to use the router will naturally allowlist the router, unknowingly opening the bypass to everyone. The trigger requires no special privilege — any EOA or contract can call the router.

### Recommendation

Pass the **economic actor** rather than the immediate caller to the extension. Two options:

1. Add a `swapper` parameter to `pool.swap` (analogous to `owner` in `addLiquidity`) that the router populates with `msg.sender` before calling the pool. The extension then checks this explicit swapper identity.
2. Alternatively, have the extension check `recipient` (the second argument it currently ignores) instead of `sender`, since `recipient` is the address that receives the output tokens and is the closest proxy for the economic beneficiary on the swap path.

Either way, the checked identity must be the same actor the pool admin intended to gate, regardless of which supported periphery contract initiates the call.

### Proof of Concept

```
Setup:
  pool deployed with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only allowed user
  allowedSwapper[pool][router] = true  // admin adds router so alice can use it

Attack (bob, not allowlisted):
  bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  router calls pool.swap(bob, ...)
  pool calls _beforeSwap(msg.sender=router, ...)
  extension checks allowedSwapper[pool][router] == true  → passes
  bob's swap executes despite not being individually allowlisted

Result:
  bob receives swap output; allowlist policy is bypassed.
  If admin does NOT allowlist the router, alice cannot use the router either —
  the only safe path is direct pool.swap calls, defeating the purpose of the periphery.
``` [3](#0-2) [1](#0-0) [6](#0-5)

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
