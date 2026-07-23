Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is set to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every non-allowlisted user can bypass the allowlist by calling the router. There is no configuration that simultaneously permits router-mediated swaps for allowlisted users and blocks non-allowlisted users.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no end-user identity forwarded — the router is `msg.sender` of that call: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

Additionally, `SwapAllowlistExtension.beforeSwap` overrides the base class function without the `onlyPool` modifier that the base class declares: [6](#0-5) 

This removes the defensive layer preventing arbitrary callers from invoking the extension directly.

The existing test only validates the allowlist against a direct `TestCaller` address, never through a router intermediary: [7](#0-6) 

## Impact Explanation

Any user not in the allowlist can execute swaps on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. Pools designed for KYC'd, institutional, or otherwise restricted participants are fully open to the public through the router path. This is a direct admin-boundary break: the pool admin's access-control policy is structurally unenforceable whenever the router is a required entry point, breaking the core invariant that the allowlist gates the economically relevant actor.

## Likelihood Explanation

The trigger is a normal, publicly documented router call requiring no special permissions, flash loans, or callbacks. The only prerequisite is that the pool admin has allowlisted the router, which is the expected operational configuration for any pool that intends to support the standard periphery. The bypass is repeatable by any unprivileged address on every router-mediated swap.

## Recommendation

1. **Pass end-user identity in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it against the allowlist. This requires a trusted encoding convention between the router and the extension.
2. **Check `recipient` instead of `sender`**: If the recipient is the economically relevant actor for the pool's policy, verify `allowedSwapper[msg.sender][recipient]` instead.
3. **Restore `onlyPool`** on `SwapAllowlistExtension.beforeSwap` to match the base class contract and prevent direct external calls.

## Proof of Concept

```solidity
// Pool admin setup (required for allowlisted users to use the router):
swapExtension.setAllowedToSwap(pool, address(router), true);

// Attacker (not in allowlist) bypasses the gate:
router.exactInputSingle(ExactInputSingleParams({
    pool:             address(pool),
    tokenIn:          address(token0),
    recipient:        attacker,
    deadline:         block.timestamp + 1,
    amountIn:         1000,
    amountOutMinimum: 0,
    zeroForOne:       false,
    priceLimitX64:    type(uint128).max,
    extensionData:    ""
}));
// Succeeds: extension sees sender = router (allowlisted), not attacker (not allowlisted).
// Swap executes; allowlist policy is defeated.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
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
