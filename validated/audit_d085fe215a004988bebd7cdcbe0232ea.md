Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks direct caller (`sender`) instead of beneficial user, enabling per-user allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension` is documented as gating swaps by swapper address per pool, but `beforeSwap` checks `sender`, which equals `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the originating user. A pool admin cannot simultaneously enable router-based swaps and enforce per-user gating: allowlisting the router bypasses the per-user check entirely, while not allowlisting it blocks all router-based swaps including those from individually allowlisted users.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` (the direct caller of `pool.swap()`) as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged as the first argument to `IMetricOmmExtensions.beforeSwap` (L149-177). `SwapAllowlistExtension.beforeSwap` then checks this value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly (L72-80). The router is therefore `msg.sender` of `pool.swap()`, so `sender` = router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

`DepositAllowlistExtension.beforeAddLiquidity` demonstrates the correct pattern: it explicitly ignores `sender` (first arg) and checks `owner` (second arg, the beneficial owner), which is set by the caller and survives router intermediation:

```solidity
// DepositAllowlistExtension.sol L32
function beforeAddLiquidity(address, address owner, ...) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The asymmetry is the root cause: `beforeAddLiquidity` gates by beneficial owner; `beforeSwap` gates by direct caller (the router).

## Impact Explanation

This is an admin-boundary break. A pool admin who deploys `SwapAllowlistExtension` to enforce per-user access control (e.g., KYC gating, private/institutional pool) faces a forced binary failure:

1. **Allowlist the router**: `allowedSwapper[pool][router] = true` passes the check for every user who calls the router, regardless of individual allowlist status. Any unprivileged user can swap in a pool intended to be access-controlled.
2. **Do not allowlist the router**: All router-based swaps revert with `NotAllowedToSwap()` even for individually allowlisted users, making the primary user-facing swap entry point unusable for the pool.

In case (1), an unprivileged path (the router) bypasses the access control the pool admin configured — a direct admin-boundary break. For compliance-gated or private pools, this allows unauthorized users to interact with pools carrying compliance obligations or favorable pricing terms.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool that deploys `SwapAllowlistExtension` expecting users to interact via the router will encounter this issue. No special permissions are required — any user can call the router. The trigger is the normal, documented swap flow.

## Recommendation

Check `recipient` (the address receiving output tokens, set by the user even when routing through the router) instead of `sender`, mirroring the pattern used by `DepositAllowlistExtension`:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Alternatively, the router could encode the originating user in `extensionData` and the extension could decode and verify it, though this requires router cooperation.

## Proof of Concept

```solidity
// Pool configured with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// allowedSwapper[pool][allowedUser] = true
// allowedSwapper[pool][router]      = false

// Case 1: router NOT allowlisted — allowedUser cannot swap via router
vm.prank(allowedUser);
router.exactInputSingle(...); // reverts NotAllowedToSwap — sender=router is not allowlisted

// Case 2: router IS allowlisted — any user bypasses the allowlist
swapAllowlist.setAllowedToSwap(pool, address(router), true);
vm.prank(unauthorizedUser);
router.exactInputSingle(...); // succeeds — sender=router is allowlisted, user check skipped
assertEq(swapAllowlist.allowedSwapper(pool, unauthorizedUser), false); // user was never allowlisted
```

**Call path for Case 2:**
`unauthorizedUser` → `MetricOmmSimpleRouter.exactInputSingle` → `MetricOmmPool.swap(msg.sender=router)` → `_beforeSwap(sender=router)` → `SwapAllowlistExtension.beforeSwap(sender=router)` → `allowedSwapper[pool][router] == true` → passes. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
