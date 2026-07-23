Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the actual end-user, enabling allowlist bypass or blocking legitimate users — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router contract, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. This produces two broken outcomes: if the router is allowlisted, every user bypasses the restriction; if the router is not allowlisted, every individually-allowlisted user who routes through it is wrongly blocked.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every registered extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router the `msg.sender` inside the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The `recipient` (the actual beneficiary of the swap) is passed as the second argument to `beforeSwap` but is silently discarded (unnamed `address`). By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` — the economically relevant party — not `sender`: [6](#0-5) 

No equivalent "check the economically relevant actor" logic exists in `SwapAllowlistExtension`.

## Impact Explanation
**Bypass path (High):** A pool admin deploys a swap allowlist to restrict trading to KYC'd addresses and allowlists the router so that allowlisted users can trade through it. Because the check is on the router address, every user — including non-allowlisted ones — can call `MetricOmmSimpleRouter.exactInputSingle` and pass the guard. The allowlist is entirely ineffective; the core invariant that only approved addresses may swap is broken.

**Blocking path (Medium):** If the admin does not allowlist the router, individually allowlisted users who attempt to trade through the router are blocked even though `allowedSwapper[pool][user]` is `true`, denying them access to the pool's swap functionality.

Both outcomes break the core invariant that the swap allowlist gates the actual swapper.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public entry point for swaps. Any user who calls it triggers the bypass. No special privileges, flash loans, or unusual token behavior are required. The only precondition is that the pool has a `SwapAllowlistExtension` configured — a standard production deployment scenario. The bypass is repeatable and requires no setup beyond calling the public router.

## Recommendation
Mirror the `DepositAllowlistExtension` pattern by checking `recipient` (the economically relevant party who receives output tokens) instead of `sender`:

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

Alternatively, introduce an `originalSender` field in the swap parameters that the router populates with `msg.sender` before calling the pool, and pass it through `extensionData` for the extension to decode and verify.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so router-mediated swaps are permitted.
3. `alice` (non-allowlisted, `allowedSwapper[pool][alice] == false`) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. The router calls `pool.swap(alice, ...)`. Inside the pool, `msg.sender = router`.
5. `_beforeSwap(router, alice, ...)` is dispatched. The extension checks `allowedSwapper[pool][router]` → `true`.
6. The swap proceeds. `alice` — never allowlisted — successfully swaps in a restricted pool.

Conversely, if step 2 is omitted (router not allowlisted), an allowlisted user `bob` (`allowedSwapper[pool][bob] == true`) who calls through the router is blocked at step 5 because `allowedSwapper[pool][router]` is `false`, even though `bob` is individually permitted.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
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
