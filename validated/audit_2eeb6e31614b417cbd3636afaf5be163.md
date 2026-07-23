Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of actual user, allowing allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the router becomes `msg.sender` inside the pool. A pool admin who allowlists the router address to enable router-mediated swaps inadvertently opens the pool to every user of the public router, completely bypassing the per-user allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` inside the pool: [4](#0-3) 

The router stores the original `msg.sender` only in transient storage for the payment callback — it is never forwarded to the pool or to any extension: [5](#0-4) 

The extension interface itself only receives `sender` as a positional argument with no mechanism to carry the original user's identity: [6](#0-5) 

The same pattern holds for `exactInput` (all hops) and `exactOutputSingle`/`exactOutput`: [7](#0-6) 

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router address — a natural step to enable router-mediated swaps for their approved users — inadvertently opens the pool to every user of the public router. Any address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput` / `exactOutputSingle`) targeting the restricted pool. The extension sees `sender = router`, which is allowlisted, and the swap proceeds. The per-user allowlist is completely bypassed. Unauthorized traders can swap against the pool's liquidity at oracle-quoted prices, directly harming LP principal in a pool that was configured to restrict access. This is a direct loss of LP funds and broken core pool functionality (the allowlist extension fails to enforce its stated invariant).

## Likelihood Explanation
The `SwapAllowlistExtension` is documented as "Gates `swap` by swapper address, per pool." A pool admin who reads this and wants to support router-mediated swaps for their allowlisted users will naturally allowlist the router address, believing the extension will still gate by individual user identity. The router is a canonical, publicly deployed periphery contract. Allowlisting it is a foreseeable and reasonable configuration step. Once the router is allowlisted, the bypass requires no special privilege — any EOA can call the router with no preconditions.

## Recommendation
The `sender` argument passed to `beforeSwap` must represent the economically relevant actor, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode the actual user address from `extensionData` when `sender` is a known router, or require callers to supply the actual user address in `extensionData` and verify it against the allowlist.

2. **Router-side**: `MetricOmmSimpleRouter` should encode the original `msg.sender` into the `extensionData` it forwards to the pool, so allowlist extensions can extract and verify the real initiator. The extension can then decode and check the actual initiator rather than the router address.

Until fixed, pool admins must not allowlist the router address on pools that intend per-user access control.

## Proof of Concept

```solidity
// Pool admin sets up a curated pool:
//   extension = SwapAllowlistExtension
//   allowedSwapper[pool][router] = true   ← admin allowlists router to support router swaps
//   allowedSwapper[pool][alice]  = false  ← alice is NOT individually allowlisted

// Alice (not allowlisted) bypasses the guard:
vm.prank(alice);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(restrictedPool),
        recipient:       alice,
        zeroForOne:      true,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:   0,
        deadline:        block.timestamp,
        tokenIn:         token0,
        extensionData:   ""
    })
);
// MetricOmmPool.swap sets sender = msg.sender = address(router)
// SwapAllowlistExtension.beforeSwap receives sender = address(router)
// allowedSwapper[pool][router] == true  → no revert
// Alice swaps successfully despite not being individually allowlisted
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
