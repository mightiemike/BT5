Audit Report

## Title
SwapAllowlistExtension Bypassed via Router: `sender` Is Router, Not End-User — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, the direct caller is the router contract, not the end-user. If the router is allowlisted — a natural admin action to enable router-mediated swaps — every user, including non-allowlisted ones, can bypass the swap gate by calling the public router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // router address, not the end-user
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is the router. The end-user's address is never consulted. `MetricOmmSimpleRouter.exactOutputSingle` calls `pool.swap` with no user identity forwarded:

```solidity
// MetricOmmSimpleRouter.sol:136-137
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

A pool admin who allowlists the router (intending to permit router-mediated swaps for allowlisted users) inadvertently opens the gate to all users, because the router is a public, permissionless contract with no per-user access control.

## Impact Explanation
The swap allowlist — the primary mechanism for pool curation — is completely ineffective for any user who routes through `MetricOmmSimpleRouter`. A curated pool that intends to restrict swaps to a specific set of users provides no restriction at all once the router is allowlisted. Non-allowlisted users can execute swaps and receive output tokens from the pool. This breaks core pool functionality (the allowlist gate) and constitutes a bypass of an admin-configured access control with direct fund-flow impact: non-permitted swaps execute and drain pool liquidity.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is the standard public swap interface. Pool admins who configure a swap allowlist and also want to support router-mediated swaps will naturally allowlist the router. The bypass requires no special knowledge — any user can call `exactOutputSingle` or `exactInputSingle` on the router against the curated pool.

## Recommendation
Forward the original initiator through the router to the pool, or redesign the allowlist to gate at the router level by checking the caller of the router rather than the caller of the pool. The cleanest fix is for `MetricOmmSimpleRouter` to pass `msg.sender` as part of `extensionData` so the extension can verify the true initiator. Alternatively, pool admin documentation must explicitly warn that allowlisting the router grants access to all users.

## Proof of Concept
```solidity
function test_nonAllowlistedUserBypassesSwapAllowlistViaRouter() public {
    // Pool admin allowlists only the router (not the user)
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    address nonAllowlistedUser = makeAddr("attacker");
    deal(address(tokenIn), nonAllowlistedUser, 10_000);
    vm.prank(nonAllowlistedUser);
    tokenIn.approve(address(router), type(uint256).max);

    uint256 balanceBefore = tokenOut.balanceOf(nonAllowlistedUser);

    // Non-allowlisted user swaps through the router — should revert, but doesn't
    vm.prank(nonAllowlistedUser);
    router.exactOutputSingle(IMetricOmmSimpleRouter.ExactOutputSingleParams({
        pool: address(pool),
        tokenIn: address(tokenIn),
        tokenOut: address(tokenOut),
        zeroForOne: true,
        amountOut: 1000,
        amountInMaximum: 10_000,
        recipient: nonAllowlistedUser,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));

    // Assert non-allowlisted user received output — allowlist was bypassed
    assertGt(tokenOut.balanceOf(nonAllowlistedUser) - balanceBefore, 0);
}
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
