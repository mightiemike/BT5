### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Complete Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap` is the **router contract**, not the originating user. A pool admin who allowlists the router to enable standard UX inadvertently grants every user on-chain access to the curated pool, completely defeating the allowlist.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct — enforced by `onlyPool`) and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap`, that argument is always `msg.sender` of the pool call:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
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

`msg.sender` of `pool.swap` is the **router**, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput` (all recursive hops in `_exactOutputIterateCallback`).

This creates a two-sided failure:

1. **Broken functionality**: If the admin allowlists individual users but not the router, those users cannot swap through the router — they must implement `IMetricOmmSwapCallback` and call the pool directly.
2. **Complete bypass**: If the admin allowlists the router (the natural fix to restore UX), every address on-chain can swap through the router regardless of individual allowlist status.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` argument (the position owner), which `MetricOmmPoolLiquidityAdder` passes through unchanged, so the deposit path does not share this flaw.

### Impact Explanation
Any non-allowlisted user can bypass a curated pool's swap allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` (or any multi-hop variant) once the pool admin has allowlisted the router. The attacker receives pool output tokens and pays input tokens — a direct, unrestricted trade on a pool that was configured to restrict access. This is a complete policy bypass with direct fund-flow consequences: the pool's LP positions are exposed to traders the pool admin explicitly excluded.

### Likelihood Explanation
The router is the primary user-facing swap interface. Any pool admin who deploys a `SwapAllowlistExtension` pool and wants their allowlisted users to use the standard periphery must allowlist the router. This is the obvious and expected operational step, making the bypass reachable in every realistic curated-pool deployment. No special timing, flash loans, or privileged access is required — a single `exactInputSingle` call suffices.

### Recommendation
The `SwapAllowlistExtension` should gate on the **economic actor** (the originating user), not the immediate caller of `pool.swap`. Two complementary fixes:

1. **Pass `tx.origin` or a user-supplied identity through `extensionData`**: The router can encode `msg.sender` (the user) into `extensionData`, and the extension can decode and verify it. This requires the extension to trust the pool's forwarding, which is already enforced by `onlyPool`.

2. **Check `sender` against a router-aware mapping**: Maintain a registry of trusted routers; when `sender` is a trusted router, extract the real user from `extensionData` and check that address instead.

The simplest safe fix is for the extension to accept an ABI-encoded `address` in `extensionData` representing the real user, verify it is non-zero, and check that address against the allowlist — while the router always encodes `msg.sender` into `extensionData` before forwarding.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is KYC'd
  allowedSwapper[pool][router] = true         // admin allowlists router for UX
  allowedSwapper[pool][attacker] = false      // attacker is NOT allowlisted

Attack (single tx, no special privileges):
  attacker calls router.exactInputSingle({
      pool: pool,
      tokenIn: token0,
      tokenOut: token1,
      amountIn: X,
      ...
  })

  router calls pool.swap(recipient, zeroForOne, X, ...)
    → msg.sender of pool.swap = router
    → pool calls _beforeSwap(router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes, attacker receives token1

Result: attacker swapped on a pool they were explicitly excluded from.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
