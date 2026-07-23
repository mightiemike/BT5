Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual trader — allowlist is fully bypassable via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` as the address that called `pool.swap()`. When a trader routes through `MetricOmmSimpleRouter`, that caller is the router contract, not the trader's EOA. The extension therefore checks whether the **router** is allowlisted, not whether the **trader** is allowlisted. If the router is added to the allowlist (the only way to enable router-based swaps), every unprivileged trader can bypass the per-address restriction by calling the router.

## Finding Description

**Call path:**

1. Trader calls `MetricOmmSimpleRouter.exactInputSingle(params)`. [1](#0-0) 

2. The router calls `IMetricOmmPoolActions(params.pool).swap(...)`. At this point `msg.sender` inside the pool is the **router address**. [2](#0-1) 

3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, forwarding the router address as `sender`. [3](#0-2) 

4. The extension hook receives `sender = router` and checks:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [4](#0-3) 

`msg.sender` here is the pool (correct), but `sender` is the **router**, not the actual trader EOA.

**Root cause:** The `sender` parameter in `IMetricOmmExtensions.beforeSwap` is defined as the direct caller of `pool.swap()`. [5](#0-4) 

When the router intermediates, this is always the router's address, making the per-trader allowlist meaningless.

**Two broken scenarios:**

- **Bypass**: Pool admin allowlists the router address (required for any router-based swap to work). Every trader — including non-allowlisted ones — can now swap by calling the router, because the check `allowedSwapper[pool][router] == true` passes for all of them.
- **DoS**: Pool admin allowlists individual EOAs but not the router. Allowlisted traders cannot swap through the router at all, because `allowedSwapper[pool][router] == false`.

The unit tests do not catch this because they call `extension.beforeSwap(swapper, ...)` directly from `vm.prank(address(pool))`, bypassing the router layer entirely. [6](#0-5) 

## Impact Explanation
The allowlist extension's core invariant — "only explicitly approved addresses may swap in this pool" — is broken for all router-mediated swaps. An unprivileged trader with no allowlist entry can execute swaps in a restricted pool simply by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`). This constitutes broken core pool functionality and an admin-boundary break: the pool admin's access control is bypassed by an unprivileged path. Severity: **High**.

## Likelihood Explanation
Any unprivileged trader can exploit this with a single router call — no special setup, no flash loan, no privileged access required. The only precondition is that the router is allowlisted (which is necessary for the pool to be usable via the router at all). The attack is repeatable on every swap.

## Recommendation
Pass the **originating trader** through the call stack rather than the immediate `msg.sender`. One approach: have the router encode the actual `msg.sender` (trader EOA) in `extensionData`, and have the extension decode and check that address. A cleaner approach is to add a `tx.origin`-based check or, preferably, have the pool accept an explicit `swapper` parameter distinct from `sender` so the router can supply the real trader address. The extension's `beforeSwap` should then validate the decoded trader address against `allowedSwapper[pool][trader]`.

## Proof of Concept

```solidity
// Foundry test sketch
function test_allowlistBypassViaRouter() public {
    // Pool admin allowlists only the router (required for router swaps)
    vm.prank(poolAdmin);
    swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

    // Unprivileged trader (NOT in allowlist) swaps via router — succeeds
    address badTrader = makeAddr("badTrader");
    assertFalse(swapAllowlist.isAllowedToSwap(address(pool), badTrader));

    deal(tokenIn, badTrader, 1e18);
    vm.startPrank(badTrader);
    IERC20(tokenIn).approve(address(router), 1e18);
    // This should revert but does NOT — router address passes the allowlist check
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: tokenIn,
        recipient: badTrader,
        amountIn: 1e18,
        amountOutMinimum: 0,
        zeroForOne: true,
        priceLimitX64: 0,
        deadline: block.timestamp + 1,
        extensionData: ""
    }));
    vm.stopPrank();
}
```

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-38)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }

  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```
