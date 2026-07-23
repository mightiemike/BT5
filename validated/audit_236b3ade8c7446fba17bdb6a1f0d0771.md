Audit Report

## Title
`SwapAllowlistExtension.beforeSwap()` checks the router address instead of the end-user, allowing any caller to bypass the per-user swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `msg.sender` of that call is the router contract, not the end user. If the pool admin allowlists the router (a natural and expected step), every unprivileged address can bypass the per-user allowlist by routing through the router, completely neutralizing the access-control mechanism.

## Finding Description
`SwapAllowlistExtension.beforeSwap()` performs its identity check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap()`, which is `msg.sender` of the pool's own `swap()` call:

In `MetricOmmPool.swap()`, the call is:
```solidity
_beforeSwap(msg.sender, recipient, ...);
```

In `MetricOmmSimpleRouter.exactInputSingle()`, the router calls:
```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The actual end-user address (`msg.sender` of the router call) is stored only in transient storage for the payment callback — it is **never forwarded to the extension**. The extension therefore sees `sender = router address`, not the real user.

A pool admin who wants allowlisted users to use the router must add the router to `allowedSwapper[pool][router]`. Once the router is allowlisted, the check `allowedSwapper[pool][sender]` passes for **every** call arriving through the router, regardless of who the actual caller is.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity()` correctly checks `owner` (the position owner) rather than `sender` (the liquidity adder), which is the correct pattern and avoids this problem entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

## Impact Explanation
Any unprivileged address can swap in a pool that the admin intended to restrict to a specific set of addresses. This constitutes an admin-boundary break: the per-user allowlist configured by the pool admin is completely bypassed by an unprivileged path (routing through the public router). Depending on the pool's purpose (institutional-only pricing, KYC-gated liquidity, rate-limited market-making), unauthorized users can access favorable oracle-driven pricing not intended for them, draining LP value at rates the LPs did not consent to. This matches the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality causing loss of funds" impact categories.

## Likelihood Explanation
The bypass requires only that the pool admin allowlists the router — a natural and expected action for any pool that wants its allowlisted users to benefit from the router's slippage protection and multi-hop routing. The `SwapAllowlistExtension` documentation says it "Gates `swap` by swapper address, per pool" with no warning that allowlisting the router collapses all users into a single identity. No special privileges, flash loans, or oracle manipulation are required; a standard `exactInputSingle` call suffices.

## Recommendation
Pass the actual end-user address through the hook chain. Two options:

1. **Router-side**: Store the real payer in transient storage and expose it via a standard interface (e.g., `IMetricOmmSwapInitiator`) that the extension can call back into the router to retrieve the originating address.
2. **Extension-side**: Change `SwapAllowlistExtension` to check `sender` only when `sender` is not a known router, and require routers to forward the real user address in `extensionData` (with the extension decoding and verifying it). The pool admin would configure trusted router addresses separately.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the position owner) rather than `sender` (the liquidity adder), which is the correct pattern.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps in a pool he was never authorized to access.

Direct pool call by Bob (`pool.swap(...)`) would correctly revert because `allowedSwapper[pool][bob]` is `false`. The bypass is exclusive to the router path. [6](#0-5) [7](#0-6) [8](#0-7)

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
