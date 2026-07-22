### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router address**, not the actual user. A pool admin who allowlists the router (the only way to permit router-mediated swaps for allowlisted users) inadvertently opens the pool to every user, because the extension checks `allowedSwapper[pool][router]` rather than the originating user's address.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed in: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly — the pool's `msg.sender` is the **router**, so `sender` delivered to the extension is the router address: [4](#0-3) 

The router never forwards the originating user's address to the pool. The pool has no mechanism to receive it. Therefore, the extension can only see the router, not the real trader.

A pool admin who wants allowlisted users to be able to use the router **must** add the router to `allowedSwapper`. Once the router is allowlisted, `allowedSwapper[pool][router] == true` satisfies the guard for every caller of the router, regardless of whether that caller is on the allowlist.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position owner explicitly passed by the caller), not `sender` (the intermediary): [5](#0-4) 

This asymmetry confirms the swap path checks the wrong actor.

### Impact Explanation

Any user can bypass a curated pool's swap allowlist by routing through `MetricOmmSimpleRouter` once the router is allowlisted. The pool receives tokens from and delivers tokens to arbitrary, non-allowlisted counterparties, defeating the curation policy entirely. This is a direct loss of the allowlist invariant and constitutes a high-severity policy bypass with fund-impacting consequences: unauthorized users trade on pools that were designed to be restricted (e.g., KYC-gated, institutional-only, or partner-only pools).

### Likelihood Explanation

Likelihood is high. The router is the primary user-facing entry point for swaps. Any pool admin who deploys a `SwapAllowlistExtension` and wants allowlisted users to use the router will naturally add the router to the allowlist — there is no other supported mechanism. The bypass is then immediately available to every user with no special setup.

### Recommendation

The `sender` delivered to extensions must represent the originating user, not the intermediary contract. Two complementary fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` pass the originating user's address as an explicit parameter to `pool.swap` (e.g., via a dedicated `sender` field in the swap signature or via `extensionData`).
2. **Extension-side**: `SwapAllowlistExtension` should read the real user from a trusted, router-supplied field rather than blindly trusting the `sender` argument when the caller is a known periphery contract.

A minimal diff for the extension, assuming the router encodes the real user in `extensionData`:

```diff
- if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
+ address realSender = extensionData.length >= 20 ? abi.decode(extensionData, (address)) : sender;
+ if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][realSender]) {
```

The cleaner fix is to add a `sender` field to the pool's `swap` signature so the router can attest the originating user, matching how `addLiquidity` already receives an explicit `owner`.

### Proof of Concept

```solidity
function test_swapAllowlistBypassViaRouter() public {
    // Setup: pool with SwapAllowlistExtension; only alice is allowlisted
    SwapAllowlistExtension ext = new SwapAllowlistExtension(address(factory));
    address pool = _deployPoolWithExtension(address(ext), _extensionOrdersWithBeforeSwap());

    vm.prank(poolAdmin);
    ext.setAllowedToSwap(pool, alice, true);

    // Admin also allowlists the router so alice can use it
    vm.prank(poolAdmin);
    ext.setAllowedToSwap(pool, address(router), true);

    // bob is NOT allowlisted — direct swap reverts
    vm.prank(bob);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    IMetricOmmPoolActions(pool).swap(bob, false, int128(1000), type(uint128).max, "", "");

    // bob routes through the router — extension sees router address, not bob
    // allowedSwapper[pool][router] == true → guard passes
    token0.approve(address(router), type(uint256).max);
    vm.prank(bob);
    // succeeds: bob bypasses the allowlist entirely
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: pool,
            recipient: bob,
            tokenIn: address(token0),
            zeroForOne: false,
            amountIn: 1000,
            amountOutMinimum: 0,
            priceLimitX64: type(uint128).max,
            deadline: block.timestamp + 1,
            extensionData: ""
        })
    );
    // bob received output despite not being on the allowlist
}
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
