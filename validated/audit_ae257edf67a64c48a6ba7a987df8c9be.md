Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, allowing any caller to bypass per-pool swap allowlists via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which is `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool receives the router as `sender`, not the actual user. Any pool admin who allowlists the router (a required step to enable router-mediated swaps) inadvertently grants every user — including those not individually allowlisted — the ability to bypass the per-pool swap allowlist.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` verbatim as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the extension call without modification: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and all other swap entry points) calls `pool.swap()` directly, with no user identity forwarded through `extensionData`: [4](#0-3) 

When a user routes through the router, the allowlist lookup becomes `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router to enable any legitimate router-mediated swap, the check passes for every caller of the router unconditionally.

`DepositAllowlistExtension` does not share this flaw because `beforeAddLiquidity` receives an explicit `owner` parameter set to the actual user, not the intermediary: [5](#0-4) 

The existing integration test confirms the design: it allowlists `callers[0]` (the intermediary contract that calls `pool.swap()`), not `users[0]` (the actual user), for the swap to succeed: [6](#0-5) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against restricted liquidity at oracle-derived prices the pool owner intended to offer only to approved parties, resulting in direct loss of LP principal and complete curation failure. This is a broken admin-boundary / broken core pool functionality with direct loss of user principal.

## Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router address. This is a routine and expected operational step: any pool that wants to support the standard periphery swap path must allowlist the router. Once that is done, the bypass is unconditionally available to every user with no further preconditions, no special tokens, and no privileged access.

## Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the actual originating user, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Short term:** The router should encode the originating `msg.sender` into `extensionData`, and `SwapAllowlistExtension.beforeSwap` should decode and check that address when `sender` is a known router.
2. **Long term:** Align the actor model across all extensions so that `beforeSwap` receives and checks the economic actor (the party whose funds move), not the routing intermediary. Add integration tests that exercise the router path against an allowlisted pool and assert that a non-allowlisted user is still rejected.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension as beforeSwap hook
  pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
    → router is allowlisted so that legitimate users can use it
  Alice (address not in allowlist) wants to swap

Attack:
  Alice calls MetricOmmSimpleRouter.exactInputSingle(...)
    → router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension.beforeSwap receives sender=router
    → check: allowedSwapper[pool][router] == true  ✓ (router was allowlisted)
    → swap proceeds, Alice receives output tokens

Result:
  Alice, who is not individually allowlisted, successfully swaps in a curated pool.
  The allowlist invariant is broken; any user can bypass it via the router.
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
