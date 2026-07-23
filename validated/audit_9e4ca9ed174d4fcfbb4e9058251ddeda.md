Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which resolves to `msg.sender` from the pool's perspective — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` is the router address, not the actual end user. A pool admin who allowlists the router to enable router-mediated swaps for permitted users inadvertently opens the pool to all users, completely bypassing the allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards `sender` verbatim to the extension. `SwapAllowlistExtension.beforeSwap` then checks: [2](#0-1) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is used, the router calls `pool.swap()` directly: [3](#0-2) 

So the check resolves to `allowedSwapper[pool][router]`. There is no mechanism in the router to pass the originating user's identity to the extension. The router passes `""` as `callbackData` and forwards `params.extensionData` from the caller — but the extension does not decode `extensionData` for identity; it reads only the `sender` positional argument.

This creates an irreconcilable conflict:
- **Router NOT allowlisted**: allowlisted users cannot use the router at all.
- **Router IS allowlisted**: `allowedSwapper[pool][router] == true`, so every user routing through the router bypasses the allowlist check, regardless of individual allowlist status.

No existing guard in the extension, pool, or router prevents this. The `isAllowedToSwap` view function returns `true` for the router address, giving the admin no indication of the problem. [4](#0-3) 

## Impact Explanation
LPs who deposit into a pool expecting restricted trading activity (e.g., only KYC-verified institutional counterparties) face fully unrestricted swap access from any address if the pool admin allowlists the router. Unauthorized users can extract value through arbitrage and front-running against the oracle-anchored pool, eroding LP principal. The allowlist — the sole access-control mechanism on the swap path — is silently nullified. This constitutes a broken core pool functionality causing loss of funds and an admin-boundary break where the pool admin's intended access restriction is bypassed by an unprivileged path.

## Likelihood Explanation
Medium. A pool admin who wants allowlisted users to benefit from the router's slippage protection and multi-hop routing will naturally allowlist the router address. The admin has no indication that doing so opens the pool to all users; the `isAllowedToSwap` view function returns `true` for the router, which appears correct. The mistake is easy to make and not surfaced by any existing guard or event. Any unprivileged trader can exploit this by routing through `MetricOmmSimpleRouter` once the router is allowlisted.

## Recommendation
The extension must gate on the actual end user's identity, not the intermediary caller. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before calling `pool.swap`. The extension decodes and checks that address when `sender` is a known router. This requires a coordinated convention between the router and the extension.

2. **Dedicated router-aware allowlist**: Extend the extension to maintain a separate `allowedRouter` mapping; when `sender` is a known router, decode the originating user from `extensionData` and check that address instead of `sender`.

## Proof of Concept
```solidity
// Pool admin sets up a restricted pool with SwapAllowlistExtension
// Admin allowlists only userA and the router (to let A use the router)
ext.setAllowedToSwap(pool, userA, true);
ext.setAllowedToSwap(pool, address(router), true);

// userB is NOT allowlisted — direct swap reverts correctly:
vm.prank(userB);
pool.swap(userB, true, 1000, type(uint128).max, "", "");
// → reverts NotAllowedToSwap ✓

// But userB routes through the router:
vm.prank(userB);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    zeroForOne: true,
    amountIn: 1000,
    recipient: userB,
    ...
}));
// → pool.swap called with msg.sender = router
// → extension checks allowedSwapper[pool][router] == true
// → swap SUCCEEDS — allowlist bypassed ✗
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L27-29)
```text
  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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
