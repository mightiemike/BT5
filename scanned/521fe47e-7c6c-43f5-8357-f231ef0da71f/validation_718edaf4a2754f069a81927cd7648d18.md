### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument passed by the pool, which is always `msg.sender` from the pool's perspective. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted, any non-allowlisted user can bypass the curated-pool gate by routing through the router.

### Finding Description

**Call chain when a user swaps through the router:**

```
user → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ...) [msg.sender = router]
     → _beforeSwap(msg.sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → checks allowedSwapper[pool][router]   ← wrong actor
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original `msg.sender` (the actual user): [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router — not the actual user: [3](#0-2) 

The pool's `swap` signature has no explicit `sender` parameter; the pool always derives it from `msg.sender`, so the router has no way to forward the original caller's identity: [4](#0-3) 

**Two concrete failure modes:**

1. **Allowlist bypass (High):** Pool admin allowlists the router address (e.g., to allow all router-mediated swaps while blocking direct pool calls). Any non-allowlisted user can now bypass the per-user gate by calling `router.exactInputSingle`. The extension sees `allowedSwapper[pool][router] = true` and passes.

2. **Broken curated-pool functionality (Medium):** Pool admin allowlists specific user addresses but not the router. Allowlisted users who use the standard periphery path (`MetricOmmSimpleRouter`) are blocked because the extension checks `allowedSwapper[pool][router] = false`. Only direct `pool.swap` calls work, making the supported periphery path unusable for curated pools.

The `DepositAllowlistExtension` does **not** share this bug — it ignores `sender` and checks `owner` (the position owner), which is correctly forwarded by `MetricOmmPoolLiquidityAdder`: [5](#0-4) 

### Impact Explanation

**Failure mode 1 (bypass):** A non-allowlisted user on a curated pool executes swaps that the pool admin intended to block. This is a direct policy bypass with fund-impacting consequences — the pool's curation invariant is broken, and the pool may receive or send tokens to/from actors it was designed to exclude.

**Failure mode 2 (broken path):** Allowlisted users cannot use the official periphery router, making the pool's swap flow partially unusable. This is broken core pool functionality.

### Likelihood Explanation

The `SwapAllowlistExtension` is a production periphery extension designed for curated pools. The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool that deploys both will encounter one of the two failure modes. Failure mode 2 is triggered by any allowlisted user who uses the router (the default path). Failure mode 1 requires the admin to allowlist the router, which is a plausible configuration when the admin wants to allow all router users while blocking direct pool calls.

### Recommendation

The pool's `swap` function should accept an explicit `swapper` parameter (the original user) that the router forwards, or the `SwapAllowlistExtension` should check `recipient` or use an alternative identity-forwarding mechanism (e.g., `extensionData`). A simpler fix is to document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level, or have the router pass the original `msg.sender` through `extensionData` and have the extension decode it with a trusted-router check.

### Proof of Concept

```solidity
// Pool admin sets up a curated pool:
swapExtension.setAllowedToSwap(address(pool), address(router), true); // allowlist the router
// (intending to allow all router users while blocking direct calls)

// Non-allowlisted attacker bypasses the per-user gate:
vm.prank(attacker); // attacker is NOT in allowedSwapper[pool][attacker]
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    ...
}));
// Extension checks allowedSwapper[pool][router] = true → swap succeeds
// Attacker bypasses the curated-pool allowlist
```

For failure mode 2:
```solidity
swapExtension.setAllowedToSwap(address(pool), allowlistedUser, true);

vm.prank(allowlistedUser);
router.exactInputSingle(...); // reverts: allowedSwapper[pool][router] = false
// allowlistedUser must call pool.swap directly — router path is broken
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
