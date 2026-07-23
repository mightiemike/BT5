Audit Report

## Title
Swap Allowlist Bypass via Router: `sender` Is Router Address, Not End User — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of `pool.swap`. When `MetricOmmSimpleRouter` intermediates, `sender` is always the router address. If the router is allowlisted (a natural admin action to enable router-mediated swaps), every user — including those not on the allowlist — can bypass the per-user gate and execute swaps on a curated pool.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // router address when called via router
  ...
);
```

`_beforeSwap` forwards this value as `sender` to all registered extensions. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap` — the router when routed. The actual end user's address is never inspected. A pool admin who calls `setAllowedToSwap(pool, address(router), true)` to permit router-mediated swaps for their curated users inadvertently opens the gate to all users, because every call through the router presents the same `sender` = router address.

The call chain is fully confirmed in production code:
1. `MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` — making the router `msg.sender` to the pool. [1](#0-0) 
2. `MetricOmmPool.swap` passes `msg.sender` (= router) as `sender` to `_beforeSwap`. [2](#0-1) 
3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` = router — never the actual user. [3](#0-2) 

No existing guard prevents this: `setAllowedToSwap` is the only mechanism to control per-address access, and it operates on the immediate caller of `pool.swap`, not the originating user. [4](#0-3) 

## Impact Explanation
Any user who is not on the per-user allowlist can execute swaps on a curated pool by routing through `MetricOmmSimpleRouter`. The allowlist — the sole access-control mechanism for swap-gated pools — is completely bypassed. This breaks core pool functionality and, depending on the pool's purpose (e.g., institutional-only, KYC-gated), constitutes a direct policy violation that can lead to unauthorized fund flows. This matches the "Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows" allowed impact.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is the canonical public periphery swap contract. Pool admins who want to support router-mediated swaps for their allowlisted users will naturally allowlist the router address. The bypass requires no special privileges — any EOA or contract can call the router. The precondition (router allowlisted) is a natural and expected admin action, not a misconfiguration.

## Recommendation
Pass the original user identity through the call chain. Two options:

1. **Preferred**: Have the pool accept an explicit `swapper` parameter (separate from `msg.sender`) that the router populates with `msg.sender` before calling the pool, and pass that through to extensions as `sender`.
2. **Alternative**: In `SwapAllowlistExtension`, do not allowlist router addresses at all; instead, document that allowlisted users must call the pool directly. This is a UX regression but closes the bypass.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension
// Admin allowlists router (intending to allow router-mediated swaps for curated users)
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// Admin does NOT allowlist attacker
// swapExtension.setAllowedToSwap(address(pool), attacker, false); // default

// Attack: attacker calls router, not pool directly
vm.prank(attacker); // attacker is NOT on the allowlist
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    ...
}));
// sender seen by SwapAllowlistExtension = address(router) → allowlisted → swap succeeds
// Invariant violated: unauthorized user executed a swap on a curated pool
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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
