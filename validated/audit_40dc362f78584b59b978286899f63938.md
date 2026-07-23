Audit Report

## Title
Router-Mediated Swap Bypasses `SwapAllowlistExtension` Per-User Gate — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. Any caller of the router bypasses the per-user allowlist if the router address is itself allowlisted, which is the natural and expected admin configuration for enabling router-mediated swaps.

## Finding Description
The call chain is confirmed by production code:

**Step 1:** `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no forwarding of the original caller: [1](#0-0) 

**Step 2:** `MetricOmmPool.swap` passes its own `msg.sender` (the router) as the `sender` argument to `_beforeSwap`: [2](#0-1) 

**Step 3:** `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, i.e., `allowedSwapper[pool][router]`: [3](#0-2) 

If the pool admin has allowlisted the router (the natural action to permit router-mediated swaps for their curated users), the check passes for **every caller** of the router, regardless of whether the actual end user is in `allowedSwapper[pool][attacker]`. There is no existing guard that recovers the original caller identity; `extensionData` is passed through but the extension does not read it.

## Impact Explanation
Any user can execute swaps on a pool whose admin intended to restrict trading to an explicit per-user allowlist, simply by routing through `MetricOmmSimpleRouter`. The pool's curation invariant — that only explicitly allowlisted end-users may trade — is broken. Disallowed users can drain LP value from a pool whose admin believed it was protected. This constitutes broken core pool functionality causing loss of funds and a broken admin-boundary invariant.

## Likelihood Explanation
The pool admin must have allowlisted the router address. This is a natural and expected configuration: a pool admin who wants to allow router-mediated swaps for their allowlisted users would add the router to `allowedSwapper`. The admin has no way to simultaneously allow router-mediated swaps AND enforce per-user identity checks, because the extension only ever sees the router as `sender`. The vulnerability is structural, not dependent on a misconfiguration that a careful admin would avoid. Any unprivileged user can exploit this by calling `exactInputSingle` on the router targeting the restricted pool.

## Recommendation
`SwapAllowlistExtension.beforeSwap` should check the `recipient` parameter (the economic beneficiary) rather than — or in addition to — `sender`, or the router should forward the original caller's address through `extensionData` so the extension can gate on the true end user. Alternatively, the extension documentation must explicitly warn that allowlisting any intermediary contract (router, multicall) opens the pool to all callers of that contract. [4](#0-3) 

## Proof of Concept
```solidity
// Pool admin setup:
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// attacker is NOT in allowedSwapper[pool][attacker]

// Attacker action:
vm.prank(attacker);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// pool.swap is called with msg.sender = router
// _beforeSwap(sender = router, ...) → allowedSwapper[pool][router] == true → passes
// Swap succeeds; attacker was never individually allowlisted
assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker)); // true: attacker never allowlisted
// but swap completed — invariant violated
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
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
