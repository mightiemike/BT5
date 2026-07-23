### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass the curated-pool allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every non-allowlisted user can bypass the curated-pool gate by calling the router. If the admin does not allowlist the router, allowlisted users cannot use the router at all, breaking the primary swap entrypoint.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 162-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

`msg.sender` of that `pool.swap()` call is the **router**, so the extension receives `sender = router`. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the position beneficiary), which the liquidity adder passes explicitly as `positionOwner` regardless of who the caller is. The swap extension has no equivalent mechanism to recover the real user identity.

---

### Impact Explanation

**Bypass path (HIGH):** A pool admin who wants allowlisted users to be able to swap through the router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, every non-allowlisted user can call `router.exactInputSingle` and the extension approves the swap because it sees `sender = router`. The curated-pool access control is completely defeated for the router entrypoint.

**Broken functionality path (MEDIUM):** If the admin does not allowlist the router, every allowlisted user who calls the router is rejected because the extension sees `sender = router` (not allowlisted). The primary periphery swap path is unusable for any pool with `SwapAllowlistExtension`, breaking core swap functionality.

Both outcomes are fund-impacting: in the bypass case, unauthorized users trade on a pool whose LP depositors expected restricted access; in the broken-functionality case, legitimate users cannot execute swaps through the supported periphery.

---

### Likelihood Explanation

The router is the primary user-facing swap entrypoint. Any pool admin who deploys a `SwapAllowlistExtension` pool and wants their allowlisted users to use the standard periphery will encounter this immediately. The bypass requires only that the admin allowlists the router (a natural operational step), after which any address can exploit it permissionlessly.

---

### Recommendation

Gate on the actual economic actor, not the direct pool caller. Two options:

1. **Mirror the deposit pattern:** Change `SwapAllowlistExtension.beforeSwap` to check `recipient` (the output beneficiary) instead of `sender`, consistent with how `DepositAllowlistExtension` checks `owner`. This is only correct if `recipient` always equals the real user, which holds for single-hop router calls but not multi-hop intermediate hops.

2. **Preferred — forward real user via `extensionData`:** Have the router encode `msg.sender` into `extensionData` and have the extension decode and check that value. The extension should fall back to checking `sender` when `extensionData` is empty (direct pool calls). This preserves the correct identity across all entrypoints.

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // to let alice use the router
4. bob (not allowlisted) calls router.exactInputSingle({pool, ...})
   → router calls pool.swap(recipient=bob, ...)
   → pool calls extension.beforeSwap(sender=router, ...)
   → extension checks allowedSwapper[pool][router] == true  ✓
   → swap executes for bob despite bob not being allowlisted
```

Direct pool call by bob would correctly revert:
```
5. bob calls pool.swap(bob, ...) directly
   → extension checks allowedSwapper[pool][bob] == false  ✗
   → NotAllowedToSwap()
```

The router is the bypass vector. The same issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in `MetricOmmSimpleRouter`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
