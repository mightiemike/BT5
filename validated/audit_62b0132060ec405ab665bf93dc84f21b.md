Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the pool to every user on-chain, completely defeating the allowlist.

## Finding Description

**Root cause:** `MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every registered extension. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap()`: [3](#0-2) 

The router stores the actual user (`msg.sender`) only in transient callback context for payment settlement — it is never forwarded to `pool.swap()` as an identity argument: [4](#0-3) 

**Inescapable dilemma:** To allow any user to swap via the router, the admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call through the router, regardless of who the originating user is. There is no configuration that simultaneously permits specific users to use the router and blocks others. [5](#0-4) 

## Impact Explanation

Any non-allowlisted user can trade on a curated pool (KYC-gated, institutional, or otherwise restricted) by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`). The pool admin's curation policy is completely nullified: unauthorized users can drain LP liquidity at oracle-anchored prices, extract fees, or trade in pools they were explicitly excluded from. This constitutes a direct loss of LP assets and broken core pool functionality (the allowlist access control), meeting the High severity threshold.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly deployed swap interface. Any pool using `SwapAllowlistExtension` that needs to support router-mediated swaps for any allowlisted user must allowlist the router, triggering the bypass for all users. The exploit requires no special privileges, no flash loans, and no multi-step setup — a single `exactInputSingle` call suffices. The condition (router allowlisted) is a necessary operational state for any pool that uses both the extension and the router.

## Recommendation

The extension must check the originating user, not the direct caller of `pool.swap()`. The preferred structural fix is to require the router to include the originating user in `extensionData` and have the extension decode and verify it. Alternatively, the pool could pass `tx.origin` as a fallback identity when `sender` is a known router, though `tx.origin` has known limitations. The cleanest protocol-level fix is to establish a convention where the router forwards `msg.sender` (the actual user) as a verifiable field in `extensionData`, and `SwapAllowlistExtension` reads and checks that field when present.

## Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only Alice is allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);
// Admin allowlists the router so Alice can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Bob (not allowlisted) calls the router directly
vm.startPrank(bob);
token0.approve(address(router), type(uint256).max);
// Succeeds — extension sees sender=router, which IS allowlisted
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// Bob successfully swapped on a pool he was explicitly excluded from
vm.stopPrank();
```

`allowedSwapper[pool][router] == true` satisfies the guard at [2](#0-1)  even though `allowedSwapper[pool][bob] == false`, so no revert occurs.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```

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
