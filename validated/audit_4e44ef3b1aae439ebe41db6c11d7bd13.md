Audit Report

## Title
SwapAllowlistExtension checks router address instead of end-user identity, allowing any user to bypass per-pool swap allowlist via an allowlisted router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension. When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract address. `SwapAllowlistExtension.beforeSwap` therefore checks whether the **router** is allowlisted rather than whether the **end user** is allowlisted, completely nullifying the access-control guarantee of the allowlist.

## Finding Description
In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the first (`sender`) argument: [1](#0-0) 

When `MetricOmmSimpleRouter.exactInputSingle` executes, it calls `pool.swap()` directly with no mechanism to forward the original caller: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router — not the end user: [3](#0-2) 

If the pool admin has called `setAllowedToSwap(pool, router, true)` — the natural configuration for any pool that wants to support router-based trading — the check passes for every caller of the router, including addresses that were never added to the allowlist and may have been explicitly excluded.

## Impact Explanation
The swap allowlist (`SwapAllowlistExtension`) is the primary per-pool access-control mechanism. Any non-allowlisted address can bypass it by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting a pool whose admin has allowlisted the router. This constitutes broken core pool functionality: restricted pools become effectively open to all users who know the router address, directly violating the allowlist invariant.

## Likelihood Explanation
The only precondition is that the pool admin has allowlisted the router — a standard, expected configuration for any pool that intends to support router-based swaps. No privileged access, malicious setup, non-standard token behavior, or special attacker capability is required. Any address can exploit this permissionlessly by calling the public router functions.

## Recommendation
The extension must check the original end user, not the intermediary router. The robust fix is to have the router store `msg.sender` (the end user) in transient storage at entry and expose it via a standard interface (e.g., `IMetricOmmRouter.swapInitiator()`). The extension then reads this value when `msg.sender` is a known router, falling back to the `sender` parameter otherwise. This preserves the allowlist invariant regardless of how many routing hops are involved.

## Proof of Concept
```solidity
// Pool admin setup (natural configuration):
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// attacker is NOT added to the allowlist

// Attacker exploits:
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
// Swap succeeds; attacker receives token1 despite not being on the allowlist.
// Pool's _beforeSwap receives sender = address(router).
// Extension checks allowedSwapper[pool][router] == true → passes.
// allowedSwapper[pool][attacker] was never set.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
