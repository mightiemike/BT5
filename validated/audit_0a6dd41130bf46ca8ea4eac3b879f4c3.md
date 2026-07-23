Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, allowing any user to bypass the swap allowlist on curated pools - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every user — including explicitly disallowed ones — can bypass the allowlist by routing through the router, rendering the security control ineffective.

## Finding Description
The call chain for a router-mediated swap is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ...)
              pool.swap: _beforeSwap(msg.sender, ...)   // msg.sender == router
                → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                     checks allowedSwapper[pool][router]
```

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `sender` (the router) against the per-pool allowlist: [2](#0-1) 

The real user's identity is stored only in transient callback context for payment purposes via `_setNextCallbackContext`, and is never forwarded to the pool's `swap()` call: [3](#0-2) 

This creates an irreconcilable dilemma for the pool admin:
- **Router not allowlisted**: No router-mediated swap works, even for users who are individually allowlisted.
- **Router allowlisted**: Every user — including explicitly blocked ones — can bypass the allowlist by routing through `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`.

There is no mechanism by which the pool or extension can recover the original end-user address from a router-mediated call, as the router only stores the payer in transient storage for the callback, not in any field forwarded to `pool.swap()`. [4](#0-3) 

## Impact Explanation
A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners). Any disallowed user can bypass this restriction entirely by calling `MetricOmmSimpleRouter.exactInputSingle` with the target pool. This is a direct admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) circumvents a security control explicitly configured by the pool admin, allowing unauthorized users to trade on pools that were explicitly configured to exclude them.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported periphery path for swaps. Any pool admin who wants allowlisted users to use the router must allowlist the router itself, which immediately opens the bypass to all users. The exploit requires no special privileges, no unusual token behavior, and no multi-transaction setup — a single `exactInputSingle` call suffices. The precondition (router allowlisted) is a realistic and expected operational scenario.

## Recommendation
The `sender` argument passed to `beforeSwap` must represent the economic actor (the end user), not the intermediary contract. The minimal safe fix is to add a check in `beforeSwap` that reverts if `sender` is a known router/intermediary, forcing all allowlisted users to call `pool.swap()` directly:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    require(!isKnownRouter[sender], RouterNotAllowed());
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Alternatively, the router should encode `msg.sender` into `extensionData` and the extension should decode it, though this requires a protocol-level convention. The preferred long-term fix is a dedicated `realSender` field in the extension interface distinct from the direct pool caller.

## Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted
extension.setAllowedToSwap(address(pool), alice, true);
// bob is NOT allowlisted

// Direct swap by bob — correctly reverts
vm.prank(bob);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(bob, true, 1000, 0, "", "");

// Pool admin must allowlist the router so alice can use it
extension.setAllowedToSwap(address(pool), address(router), true);

// Now bob bypasses the allowlist via the router
vm.prank(bob);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: bob,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// bob's swap succeeds — allowlist bypassed
// The extension checks allowedSwapper[pool][router] (true) and passes, never inspecting bob's identity
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
