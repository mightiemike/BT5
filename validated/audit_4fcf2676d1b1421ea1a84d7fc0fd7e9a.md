Audit Report

## Title
`SwapAllowlistExtension` Allowlist Bypassed via Router When Router Address Is Allowlisted - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap` call. When swaps are routed through `MetricOmmSimpleRouter`, the router contract becomes `msg.sender` at the pool. A pool admin who allowlists the router address to permit router-mediated swaps for approved users inadvertently grants swap access to every address on-chain, completely defeating the allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` at the pool level: [3](#0-2) 

The same pattern applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

When the router is allowlisted (`allowedSwapper[pool][router] = true`), the extension check `allowedSwapper[msg.sender][sender]` evaluates to `allowedSwapper[pool][router]`, which is `true` for every caller routing through the router — regardless of who the originating user is. There is no mechanism in the extension to identify the actual end user. The existing guard is structurally insufficient: it checks the intermediary (router), not the originating trader.

## Impact Explanation
A pool admin who deploys `SwapAllowlistExtension` to restrict trading to specific counterparties and allowlists the router to give those counterparties normal UX (slippage protection, multi-hop, deadline checks) will find that any address on-chain can bypass the allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` or any other router entry point. LP positions are then exposed to unrestricted swap flow from actors the admin explicitly intended to exclude, enabling adverse selection and unauthorized extraction of LP value. This constitutes an admin-boundary break where an unprivileged path bypasses a configured access control, with direct impact on LP principal.

## Likelihood Explanation
The router is the primary user-facing entry point deployed alongside the pool. Any pool admin who wants allowlisted users to have standard UX will naturally allowlist the router. The bypass is then reachable by any unprivileged address with zero special access, requiring only a standard `exactInputSingle` call. The only configuration that avoids the bypass — not allowlisting the router — breaks the router for every user including approved ones, making it an unlikely production choice. The bypass is repeatable and requires no special privileges.

## Recommendation
The extension must verify the originating user, not the immediate pool caller. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks that address instead of (or in addition to) `sender`.
2. **Trusted router registry**: Maintain a registry of trusted routers in the extension; when `sender` is a known router, extract and verify the real user from `extensionData`. When `sender` is not a known router, check `sender` directly.

Either approach must be applied consistently to `DepositAllowlistExtension` as well, since the same router-mediation pattern applies to `addLiquidity` calls.

## Proof of Concept
```
Setup:
  pool P configured with SwapAllowlistExtension
  allowedSwapper[P][alice]  = true   (alice is the approved trader)
  allowedSwapper[P][router] = true   (admin allowlists router so alice can use it)

Attack (executed by bob, who is NOT allowlisted):
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool:      P,
      recipient: bob,
      ...
  })

  Router calls P.swap(bob, ...)
    → pool sets sender = address(router)   [MetricOmmPool.sol L231]
    → _beforeSwap passes sender=router to SwapAllowlistExtension
    → extension checks allowedSwapper[P][router] == true  ✓  [SwapAllowlistExtension.sol L37]
    → swap executes, bob receives output tokens

Result:
  bob, a non-allowlisted address, successfully swaps against the pool,
  bypassing the access control the admin configured.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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
