Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the router contract when a user routes through `MetricOmmSimpleRouter`. Any pool admin who allowlists the router so approved users can access the standard swap UX inadvertently opens the pool to every caller of the router, completely defeating the per-user access gate.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that value against the allowlist: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` at the pool: [3](#0-2) 

The result is that the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. The pool admin faces an impossible configuration: not allowlisting the router breaks router access for approved users; allowlisting the router opens the pool to all router callers. There is no configuration that achieves "only allowlisted users may swap through the router."

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the explicit `owner` parameter (the real depositor), not the direct caller: [4](#0-3) 

No equivalent "real swapper" parameter exists in the swap path — the pool only forwards `msg.sender`.

## Impact Explanation

A pool deployer who configures `SwapAllowlistExtension` to restrict swaps to KYC-verified counterparties, whitelisted market makers, or to exclude known MEV extractors, and then allowlists the router so those approved users can access the standard swap UX, unknowingly opens the pool to all router callers. Any non-allowlisted address can call `router.exactInputSingle()`, `exactInput()`, `exactOutputSingle()`, or `exactOutput()` and execute swaps against the pool. LP principal is directly at risk: the pool's oracle-priced liquidity is accessible to actors the pool admin explicitly intended to exclude, enabling value extraction from LP positions the allowlist was designed to prevent. This constitutes a direct loss of user principal above Sherlock thresholds and broken core pool access-control functionality.

## Likelihood Explanation

The scenario requires no privileged escalation, no malicious setup, and no non-standard tokens. A pool admin deploys a pool with `SwapAllowlistExtension`, allowlists specific user addresses, and then allowlists the router — a natural operational step since the router is the canonical swap entry point. Any unprivileged user then calls the router and bypasses the gate. The attacker only needs to call a public router function.

## Recommendation

The `beforeSwap` hook receives `sender` (direct pool caller) and `recipient`; neither is the true end-user when the router intermediates. Three sound fixes:

1. **Pass the real user through `extensionData`**: The router already forwards `extensionData` to the pool. Require callers to embed their address in `extensionData`; the extension decodes and verifies it.
2. **Mirror the deposit pattern**: Add a `swapper` parameter to `pool.swap()` analogous to `owner` in `addLiquidity`. The pool passes this declared address to the extension; the router passes `msg.sender` as `swapper`.
3. **Check `recipient` instead of `sender`** only if the pool's design guarantees `recipient == actual user`, which is not always true.

Until fixed, pool admins must be warned that allowlisting the router is equivalent to calling `setAllowAllSwappers(pool, true)`.

## Proof of Concept

```solidity
// Setup:
// 1. Pool configured with SwapAllowlistExtension
// 2. Pool admin allowlists alice and the router
//    extension.setAllowedToSwap(pool, alice, true);
//    extension.setAllowedToSwap(pool, address(router), true); // needed for alice to use router
// 3. bob is NOT allowlisted

// Attack:
vm.prank(bob);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 10_000,
    amountOutMinimum: 0,
    recipient: bob,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Extension checks allowedSwapper[pool][router] = true → no revert
// bob swaps successfully despite allowedSwapper[pool][bob] = false
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-38)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-39)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
