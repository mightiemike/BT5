Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Trader, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of that call, so the allowlist check evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. If the router is allowlisted — the natural configuration for any pool supporting normal trading flows — every user can bypass the per-user swap restriction entirely by routing through the router.

## Finding Description
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`, forwarding `msg.sender` as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` passes that `sender` value verbatim to the extension hook: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly — making the router `msg.sender` of the pool call, not the end user: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The structural asymmetry is confirmed by `DepositAllowlistExtension.beforeAddLiquidity`, which correctly checks `owner` (the actual economic actor, explicitly passed by the caller) rather than `sender` (the payer/intermediary): [6](#0-5) 

The `recipient` address — the actual economic beneficiary of the swap — is available as the second (currently ignored) parameter of `beforeSwap` but is never checked. [7](#0-6) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC'd counterparties) is fully bypassed for any user who routes through `MetricOmmSimpleRouter` once the router is allowlisted on that pool. The wrong value checked is `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]` — the per-user restriction is never evaluated. Unauthorized parties can drain pool liquidity at oracle-quoted prices, constituting a direct loss of LP principal and a broken core pool security control. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation
The `MetricOmmSimpleRouter` is the primary supported swap entrypoint. Any pool admin who wants allowlisted users to trade through the router must allowlist the router address — the only way to make the extension compatible with the router. This is a predictable, near-certain misconfiguration. Once the router is allowlisted, any unprivileged user can call `exactInputSingle` or any other router entry point to trade on the restricted pool. No special privileges are required; the trigger is a standard router call.

## Recommendation
Change `SwapAllowlistExtension.beforeSwap` to check `recipient` (the second parameter, currently ignored) instead of `sender`, since `recipient` is the address that receives output tokens and represents the actual economic beneficiary. This mirrors how `DepositAllowlistExtension` checks `owner` rather than `sender`. Alternatively, require the router to pass the real user address through `extensionData` and have the extension decode and verify it — but this approach requires router cooperation and must guard against spoofing by unprivileged callers. The `recipient`-based fix is simpler and consistent with the existing deposit allowlist design pattern.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — allowlisting `MetricOmmSimpleRouter` so that allowlisted users can trade through the canonical UI path.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: attacker, ...})`.
5. Router calls `pool.swap(recipient=attacker, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, recipient=attacker, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → passes.
8. `attacker` successfully swaps on the restricted pool; `allowedSwapper[pool][attacker]` is never evaluated.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
