Audit Report

## Title
`SwapAllowlistExtension` checks router address as swapper identity instead of originating user, enabling complete allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender` — the router contract when users swap via `MetricOmmSimpleRouter`. A pool admin who allowlists the router address to enable standard UX inadvertently grants swap access to every user, completely defeating the allowlist. Conversely, allowlisting specific user addresses makes those users unable to swap through the router at all.

## Finding Description
**Root cause — extension checks `sender`, which is the pool's `msg.sender`:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the forwarded swapper identity: [1](#0-0) 

**Pool forwards its own `msg.sender` as `sender`:**

`MetricOmmPool.swap` passes `msg.sender` directly to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` then encodes that value as the first argument to every configured extension: [3](#0-2) 

**Router is the pool's `msg.sender`, not the originating user:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The pool's `msg.sender` is therefore the router contract address: [4](#0-3) 

The same holds for `exactInput` (all hops use `address(this)` as payer after the first): [5](#0-4) 

And for `exactOutputSingle` and `exactOutput`, which also call `pool.swap` directly from the router: [6](#0-5) 

**No existing guard corrects this:** There is no mechanism in the pool, router, or extension to recover the originating user's address. The `extensionData` field passed by the router is `""` (empty) for all single-hop paths, so the extension cannot decode a real user address from it.

## Impact Explanation
Two fund-impacting outcomes:

**Bypass (High):** A pool admin who allowlists the router address to enable router-mediated swaps inadvertently opens the pool to every caller. Because all users reach the pool through the same router contract, `allowedSwapper[pool][router] == true` passes for every originating address — including addresses the admin explicitly never allowlisted. Any unprivileged user can drain LP liquidity at oracle prices.

**Broken core flow (Medium):** A pool admin who allowlists specific user addresses (the intended design) makes those users unable to swap through the router, because the router address is not allowlisted. The only reachable path is a direct `pool.swap()` call, which is not the supported periphery UX. LP liquidity becomes effectively illiquid for the intended counterparties.

Both outcomes constitute direct loss of LP principal or broken core swap functionality, meeting Sherlock High/Medium thresholds.

## Likelihood Explanation
The router is the canonical, documented swap entrypoint. A pool admin configuring a curated allowlist pool will naturally test swaps through the router, find them blocked (because the router is not allowlisted), and allowlist the router address to restore functionality — unknowingly opening the pool to all users. No privileged escalation beyond the pool admin's own configuration action is required. The trigger is a routine, expected admin action within the semi-trusted scope.

## Recommendation
The extension must check the originating user, not the intermediary. The cleanest fix:

**Pass the real user through `extensionData`:** The router encodes `msg.sender` (the originating user) into `extensionData` before calling `pool.swap`. `SwapAllowlistExtension.beforeSwap` decodes and checks that address. For direct pool calls (where `sender` already equals the real user), the extension falls back to checking `sender` when `extensionData` is empty. Pool admins must document that the extension requires this encoding when used with the router.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` extension.
2. Pool admin calls `setAllowedToSwap(pool, address(router), true)` to enable router-mediated swaps.
3. Unprivileged `attacker` (never individually allowlisted) calls `router.exactInputSingle(...)` targeting the pool.
4. Inside `pool.swap`, `msg.sender == router`. `_beforeSwap` forwards `sender = router` to the extension.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router] == true` → passes.
6. `attacker` completes the swap and receives output tokens from LP reserves despite never being allowlisted.

Conversely, if the admin allowlists only `userA` (not the router), `userA` calling `router.exactInputSingle` causes the extension to check `allowedSwapper[pool][router] == false` → reverts with `NotAllowedToSwap`, making the pool inaccessible through the standard UX. [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
