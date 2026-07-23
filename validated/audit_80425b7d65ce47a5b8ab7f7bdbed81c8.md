Audit Report

## Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router to enable router-mediated swaps for their allowlisted users simultaneously opens the allowlist to every address on the network.

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` keys the allowlist on `sender` (= router) and `msg.sender` (= pool): [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly — the router becomes `msg.sender` to the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` gates on `owner`, an explicit argument that survives router indirection and is not collapsed to the intermediary's address: [6](#0-5) 

The root cause is that `sender` in the swap hook always resolves to the immediate pool caller (the router), not the originating user. Once the router is allowlisted — a prerequisite for any allowlisted user to use the standard UI — the check `allowedSwapper[pool][router]` passes for every caller of the router, regardless of whether that caller is allowlisted.

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` faces an inescapable dilemma: allowlisting the router (required for legitimate users to use the standard periphery) simultaneously grants swap access to every address on the network. Non-allowlisted users can extract value from LPs who deposited under the assumption that only vetted counterparties would trade against them. This is a direct, complete bypass of a configured access-control guard with fund-impacting consequences for LPs on curated pools. **Impact: High.**

## Likelihood Explanation

The router is the standard, documented periphery entry point for swaps. Pool admins who want their allowlisted users to be able to use the router must allowlist it. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle` on the router pointing at the curated pool. The condition is trivially reachable in any realistic deployment of `SwapAllowlistExtension`.

## Recommendation

The extension must recover the original user identity rather than trusting the `sender` argument:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before forwarding to the pool. The extension decodes and verifies it. This requires a trusted router or a signed proof.
2. **Check both `sender` and `recipient`**: Require that both the caller (`sender`) and the output recipient are allowlisted, so routing through an allowlisted router does not grant access to a non-allowlisted recipient.
3. **Document incompatibility**: If neither fix is applied, document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory/extension-config validation layer.

## Proof of Concept

```solidity
// Setup: pool admin deploys pool with SwapAllowlistExtension
// Admin allowlists alice (legitimate user) and the router (to let alice use the UI)
swapAllowlist.setAllowedToSwap(pool, alice, true);
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// Attack: bob (not allowlisted) bypasses the guard via the router
// The extension sees sender = address(router), which IS allowlisted → passes
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        recipient: bob,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        tokenIn: token0,
        extensionData: ""
    })
);
// bob successfully swaps on a pool he was never allowlisted for
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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
