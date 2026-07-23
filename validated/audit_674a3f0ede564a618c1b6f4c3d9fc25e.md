Audit Report

## Title
SwapAllowlistExtension receives router address as `sender` instead of end-user, enabling full allowlist bypass for any user routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap` passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, so it checks whether the **router** is allowlisted, not the end-user. If the pool admin allowlists the router to permit router-mediated swaps on a restricted pool, every user on the network can bypass the allowlist by routing through `MetricOmmSimpleRouter`.

## Finding Description
The call chain is confirmed in production code:

**Step 1** — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no end-user identity forwarding. `msg.sender` to the pool is the router address. [1](#0-0) 

**Step 2** — `MetricOmmPool.swap` passes `msg.sender` (the router) as the first argument to `_beforeSwap`: [2](#0-1) 

**Step 3** — `SwapAllowlistExtension.beforeSwap` receives `sender = router` and checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`. The end-user address is never seen by the extension: [3](#0-2) 

The pool admin faces a structurally broken choice:
- **Do not allowlist the router** → all router-mediated swaps revert for everyone, including legitimate users.
- **Allowlist the router** → every address on the network bypasses the allowlist by calling any of `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router.

There is no mechanism to allowlist individual end-users who route through the router. The same pattern applies to all four router entry points. [4](#0-3) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd users, whitelisted market makers) provides zero protection against any user who routes through `MetricOmmSimpleRouter`. Any non-allowlisted user can execute swaps against a restricted pool, causing unauthorized trading against the pool in ways the pool admin explicitly intended to prevent. This constitutes broken core pool functionality (the allowlist guard) and enables unauthorized swaps that may cause direct loss to LPs on curated pools — matching the "Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows" allowed impact.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap interface and is permissionless. The only precondition is that the pool admin has allowlisted the router — a natural and expected action to support router-mediated swaps on a restricted pool. No privileged access, no malicious setup, and no non-standard token behavior is required. Any user who discovers the restriction can trivially route through the router.

## Recommendation
The `sender` parameter passed to extensions should represent the true economic initiator of the swap, not the immediate `msg.sender`. Two approaches:

1. **Pool-side (preferred)**: Add an explicit `swapper` parameter to `pool.swap()` that the router populates with `msg.sender` (the end-user). The pool passes this `swapper` to extensions instead of its own `msg.sender`. This mirrors how Uniswap v4 separates `sender` from `msgSender` in hook calls.
2. **Router-side**: `MetricOmmSimpleRouter` should forward the end-user's address as part of `extensionData`, and `SwapAllowlistExtension` should decode it when `msg.sender` is a known router.

## Proof of Concept
```solidity
// Foundry integration test sketch
function test_allowlistBypassViaRouter() public {
    // Setup: pool with SwapAllowlistExtension
    // Router is allowlisted (required for any router swap to work on restricted pool)
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // endUser is NOT in allowedSwapper — should be blocked

    // endUser routes through router — extension sees router as sender, passes check
    vm.prank(endUser);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        recipient: endUser,
        deadline: block.timestamp,
        priceLimitX64: 0,
        extensionData: ""
    }));
    // swap succeeds — allowlist bypassed by non-allowlisted endUser
}
```

### Citations

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
