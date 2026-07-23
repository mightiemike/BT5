Audit Report

## Title
`SwapAllowlistExtension` checks router address as `sender` instead of actual end-user, allowing full allowlist bypass via router — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded from the pool, which is always `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. Any pool admin who allowlists the router to support the standard periphery flow unknowingly opens the gate to every user, rendering the per-user allowlist completely ineffective.

## Finding Description
**Root cause — `MetricOmmPool.swap` passes `msg.sender` as `sender`:** [1](#0-0) 

When called via `MetricOmmSimpleRouter.exactInputSingle`, `msg.sender` is the router address, not the end-user.

**`ExtensionCalling._beforeSwap` forwards it unchanged:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` checks that forwarded address:** [3](#0-2) 

The check resolves to `allowedSwapper[pool][router]`. If the router is allowlisted (required for any router-mediated swap to work), this check passes for every caller of the router regardless of their identity.

**The router never supplies the real user's address as `sender`:** [4](#0-3) 

The router calls `pool.swap(params.recipient, ...)` with no mechanism to inject the actual `msg.sender` (the end-user) into the pool's `sender` argument.

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` (the position owner, the economically relevant actor) rather than `sender` (the caller/payer): [5](#0-4) 

For deposits, `addLiquidity` passes `owner` as a distinct argument representing the actual beneficiary. No equivalent "real user" field exists in the swap path.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses. Once the router is allowlisted (the only way to support the standard periphery flow), the allowlist is completely ineffective: any address can call `router.exactInputSingle(...)` and the extension approves the swap because it sees the allowlisted router, not the actual caller. Non-allowlisted users can execute swaps against a pool designed to be access-controlled, constituting broken core pool functionality and a direct admin-boundary break — the pool admin's access control cap is bypassed by an unprivileged path.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point. Any pool admin who configures `SwapAllowlistExtension` and also wants users to use the router will add the router to the allowlist, unknowingly opening the gate to all users. The bypass requires no special privileges, no flash loans, and no unusual token behavior — a single standard router call suffices. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Recommendation
Pass the original end-user address through the swap path so the extension can gate the economically relevant actor:

1. **Mirror the deposit pattern**: Extend `IMetricOmmPoolActions.swap` with an explicit `swapper` address (defaulting to `msg.sender` for direct calls) and pass that to `_beforeSwap` instead of `msg.sender`. The router would supply its own `msg.sender` (the actual user) at call time.
2. **Short-term mitigation**: Pool admins must not rely on `SwapAllowlistExtension` for per-user access control on pools accessible through the router. They should allowlist individual users directly and not allowlist the router address.

## Proof of Concept
**Setup:**
- Pool configured with `SwapAllowlistExtension`
- `alice` is NOT in the allowlist (`allowedSwapper[pool][alice] == false`)
- `MetricOmmSimpleRouter` IS in the allowlist (`allowedSwapper[pool][router] == true`)

**Attack:**
```
alice → router.exactInputSingle({pool: curated_pool, tokenIn: token0, recipient: alice, ...})
       → pool.swap(recipient=alice, ...)   [msg.sender = router]
       → _beforeSwap(sender=router, ...)
       → SwapAllowlistExtension.beforeSwap(sender=router, ...)
       → allowedSwapper[pool][router] == true  ✓  (passes!)
       → alice's swap executes against the curated pool
```

**Expected:** `NotAllowedToSwap` revert because `alice` is not allowlisted.  
**Actual:** Swap succeeds because the router is allowlisted and the extension never sees `alice`'s address.

A Foundry test can confirm this by: (1) deploying a pool with `SwapAllowlistExtension`, (2) calling `setAllowedToSwap(pool, router, true)` but not for `alice`, (3) calling `router.exactInputSingle(...)` from `alice` and asserting the swap succeeds rather than reverting with `NotAllowedToSwap`.

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
