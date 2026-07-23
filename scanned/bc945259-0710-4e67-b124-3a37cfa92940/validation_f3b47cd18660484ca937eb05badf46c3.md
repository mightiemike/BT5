Looking at the `SwapAllowlistExtension` and how `MetricOmmSimpleRouter` interacts with it, I can trace a concrete bypass path.

**Key code path:**

In `MetricOmmPool.swap()`, `msg.sender` is passed as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards it to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user swaps through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

So `msg.sender` seen by the pool is the **router address**, not the actual end user. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

---

### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` from the pool's perspective. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual end user. A pool admin who allowlists the router to enable router-mediated swaps for permitted users inadvertently opens the pool to all users, completely bypassing the allowlist.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to `SwapAllowlistExtension.beforeSwap`. The extension checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router when `MetricOmmSimpleRouter` is used. The check therefore resolves to `allowedSwapper[pool][router]`.

This creates an irreconcilable conflict for any pool admin who wants to restrict swaps to specific addresses while still supporting the router:

- **If the router is NOT allowlisted**: allowlisted users cannot use `MetricOmmSimpleRouter` at all — the pool is incompatible with the router.
- **If the router IS allowlisted**: `allowedSwapper[pool][router] == true`, so the check passes for every user who routes through the router, regardless of whether they are individually allowlisted.

There is no configuration that achieves the intended goal: allow only specific users to swap, whether directly or via the router.

### Impact Explanation
LPs who deposit into a pool expecting restricted trading activity (e.g., only KYC-verified institutional counterparties) face fully unrestricted swap access from any address if the pool admin allowlists the router. Unauthorized users can extract value through arbitrage and front-running against the oracle-anchored pool, eroding LP principal. The allowlist — the sole access-control mechanism on the swap path — is silently nullified.

### Likelihood Explanation
Medium. A pool admin who wants allowlisted users to benefit from the router's slippage protection and multi-hop routing will naturally allowlist the router address. The admin has no indication that doing so opens the pool to all users; the `isAllowedToSwap` view function returns `true` for the router, which appears correct. The mistake is easy to make and not surfaced by any existing guard or event.

### Recommendation
The extension must gate on the actual end user's identity, not the intermediary caller. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData` before calling `pool.swap`. The extension decodes and checks that address. This requires a coordinated convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the actual user. This is imprecise for multi-hop paths where intermediate recipients are the router itself.

3. **Dedicated router-aware allowlist**: Extend the extension to maintain a separate `allowedRouter` mapping; when `sender` is a known router, decode the originating user from `extensionData` and check that address instead.

### Proof of Concept
```solidity
// Pool admin sets up a restricted pool
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool deployed with ext in beforeSwap order

// Admin allowlists only user A and the router (to let A use the router)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
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
