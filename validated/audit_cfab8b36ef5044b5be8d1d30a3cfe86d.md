Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the original user, breaking the per-user swap allowlist for all router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` equal to `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, that value is the router address, not the original user. The allowlist lookup therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, making the per-user gate either permanently broken (allowlisted users cannot use the router) or trivially bypassable (if the router is allowlisted, all users pass).

## Finding Description
`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly, making the router contract `msg.sender` at the pool boundary. [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`. [2](#0-1) 

`ExtensionCalling._beforeSwap` ABI-encodes and forwards that same `sender` to every configured extension unchanged. [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router — never the original EOA. [4](#0-3) 

There is no mechanism in the call chain to thread the original user's address through to the extension. The two outcomes are mutually exclusive: either allowlisted users are blocked from the router, or the router is allowlisted and the gate is open to everyone.

## Impact Explanation
Any pool that deploys `SwapAllowlistExtension` to restrict swaps to specific users will silently block those users from using `MetricOmmSimpleRouter`, which is the primary public swap interface. A pool admin who allowlists the router address as a workaround inadvertently opens the gate to all users, defeating the purpose of the extension entirely. This constitutes broken core pool functionality causing an unusable swap flow and a complete allowlist bypass — both qualifying impacts under the contest gate.

## Likelihood Explanation
The router is the standard public swap path. Any pool that uses `SwapAllowlistExtension` and expects users to swap via `MetricOmmSimpleRouter` will hit this immediately on first use. No special attacker setup is required; the broken behavior is triggered by normal usage from any allowlisted user who calls the router.

## Recommendation
Thread the original user's identity explicitly through the call chain. One approach: add a dedicated `swapper` parameter to the `beforeSwap` hook signature that the pool populates from a source other than `msg.sender` (e.g., via `extensionData` passed by the router, or a separate transient storage slot set by the router before calling `pool.swap`). The router already uses transient storage for callback context (`_setNextCallbackContext`), so a similar pattern could carry the original `msg.sender` for extension consumption.

## Proof of Concept
```solidity
// Pool has SwapAllowlistExtension; only userA is allowlisted.
swapExtension.setAllowedToSwap(address(pool), userA, true);

// userA swaps directly → passes (sender = userA ✓)
vm.prank(userA);
pool.swap(userA, false, 1000, type(uint128).max, "", "");

// userA swaps via router → REVERTS (sender = router, not allowlisted ✗)
vm.prank(userA);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: userA,
    ...
}));
// ↑ reverts with NotAllowedToSwap even though userA is allowlisted

// Admin allowlists the router to "fix" it:
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// userB (NOT allowlisted) bypasses the gate via router:
vm.prank(userB);
router.exactInputSingle(...); // succeeds — allowlist fully bypassed
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
