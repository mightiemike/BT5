Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Originating User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which is the router address when a user enters through `MetricOmmSimpleRouter`. `SwapAllowlistExtension.beforeSwap` checks this `sender` against the allowlist, so it evaluates the router's allowlist status rather than the actual user's. Any pool admin who allowlists the router to support legitimate users inadvertently grants unrestricted swap access to all users.

## Finding Description
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` at line 230–231, passing the direct caller as `sender`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whatever address the pool forwarded: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` with no forwarding of the originating user's address — the router is `msg.sender` to the pool: [3](#0-2) 

This creates an inescapable dilemma: if the admin does not allowlist the router, allowlisted users cannot use the router at all. If the admin does allowlist the router (the natural operational choice), every user — allowlisted or not — can bypass the gate by routing through the same public contract. The extension has no mechanism to distinguish the real initiator from the router.

## Impact Explanation
The allowlist is the sole mechanism for restricting who may trade on a curated pool. When the router is allowlisted, any unprivileged user can call `router.exactInputSingle()` and the extension will see `sender = router`, pass the check, and execute the swap. Unauthorized users gain full swap access, which can result in direct loss of LP value through uninvited price impact, fee extraction, or MEV on pools designed to be restricted. This constitutes a broken core pool access-control mechanism causing potential loss of LP assets.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap entry point. Pool admins who deploy `SwapAllowlistExtension` and want their allowlisted users to be able to use the router must allowlist the router address — this is the expected operational path. The bypass is reachable on any production pool using the allowlist extension with router support enabled, with no special preconditions for the attacker beyond calling a public function.

## Recommendation
The router must forward the originating user's identity to the pool so the extension can gate the correct actor:

1. Add a `swapOnBehalf` parameter to `pool.swap()` (or a separate entry point) that accepts the real initiator address, verified by the router before forwarding. The extension then checks this forwarded address instead of `sender`.
2. Alternatively, change `SwapAllowlistExtension.beforeSwap` to read the real caller from a trusted-router-encoded `extensionData` field, with the extension verifying the encoding came from a trusted router address.

Until fixed, pool admins must be warned that allowlisting the router grants unrestricted swap access to all users.

## Proof of Concept
```solidity
// Pool configured with SwapAllowlistExtension.
// Admin allowlists alice (legitimate user) and the router (for usability).
// Bob is NOT allowlisted.

// Bob calls the router — extension sees sender = router, not Bob.
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// allowedSwapper[pool][router] == true → swap succeeds, Bob bypasses allowlist.

// Direct call by Bob (without router) correctly reverts:
pool.swap(bob, true, 1000e18, 0, "", "");
// allowedSwapper[pool][bob] == false → NotAllowedToSwap()
``` [4](#0-3) [5](#0-4) [6](#0-5)

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
