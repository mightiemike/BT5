The code confirms the claim exactly. All three cited code paths are verified:

- `MetricOmmPool.swap()` passes `msg.sender` (= router) to `_beforeSwap` [1](#0-0) 
- `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly with no originating EOA forwarded [2](#0-1) 
- `SwapAllowlistExtension.beforeSwap()` checks only `allowedSwapper[msg.sender][sender]` where `sender` = router [3](#0-2) 

---

Audit Report

## Title
Router Address Substitution Bypasses Per-User Swap Allowlist in `SwapAllowlistExtension` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter against `allowedSwapper[pool][sender]`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `swap()` receives `msg.sender = router`, so `sender` forwarded to the extension is the router address, not the originating EOA. Any pool that allowlists the router address grants unrestricted swap access to every user of that router, defeating the per-user gate entirely.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- router address when called via router
  recipient,
  ...
);
```

When the pool is called by `MetricOmmSimpleRouter.exactInputSingle()`, `msg.sender` inside the pool is the router contract. The router makes no attempt to forward the originating EOA:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // extensionData is caller-supplied; no originating sender injected
  );
```

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` = pool and `sender` = router. If `allowedSwapper[pool][router] = true`, this condition is satisfied for every caller of the router. The originating EOA's address is never examined.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd users, whitelisted counterparties) is fully bypassed for any user who routes through an allowlisted router. Non-allowlisted EOAs can execute swaps and receive output tokens from the pool. This constitutes unauthorized token outflow and breaks the core access-control invariant the extension is designed to enforce. Severity: High — direct unauthorized fund flow from a restricted pool to non-allowlisted addresses.

## Likelihood Explanation
The scenario is not hypothetical: a pool admin who wants to permit router-based swaps while restricting direct callers would naturally allowlist the router address. The router is a public, permissionless contract. Any EOA can call `exactInputSingle` with the target pool, and the bypass requires no special privileges, no malicious setup, and no non-standard token behavior.

## Recommendation
The extension must check the originating user, not the immediate pool caller. Two viable approaches:

1. **Pass the originating sender explicitly**: Add an `originSender` field to `extensionData` that the router populates with `msg.sender` before calling the pool, and have the extension decode and check it.
2. **Preferred — allowlist at the router level**: Do not allowlist the router address in the extension. Instead, require each individual EOA to be allowlisted. Document that the extension is incompatible with router intermediaries unless per-user allowlisting is enforced.

## Proof of Concept

```solidity
function test_nonAllowlistedEOABypassesViaRouter() public {
    // Setup: pool with SwapAllowlistExtension, only router is allowlisted
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // nonAllowlistedEOA is NOT in allowedSwapper

    address nonAllowlistedEOA = makeAddr("attacker");
    deal(address(token0), nonAllowlistedEOA, 10_000e18);
    vm.prank(nonAllowlistedEOA);
    token0.approve(address(router), type(uint256).max);

    uint256 token1Before = token1.balanceOf(nonAllowlistedEOA);

    // This should revert with NotAllowedToSwap but does NOT
    vm.prank(nonAllowlistedEOA);
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1_000,
        amountOutMinimum: 0,
        recipient: nonAllowlistedEOA,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));

    // Non-allowlisted EOA received token1 — allowlist bypassed
    assertGt(token1.balanceOf(nonAllowlistedEOA), token1Before);
}
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
