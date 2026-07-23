Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of actual user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address. If the pool admin allowlists the router so that allowlisted users can trade conveniently, every non-allowlisted user can bypass the per-user restriction by calling through the router. The curated pool's access control is silently nullified.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool and thus the `sender` forwarded to the extension: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the economic actor), not `sender` (the operator/adder), because `addLiquidity` separates these two identities: [6](#0-5) 

The swap path has no equivalent separation — `sender` is the only identity available, and it collapses to the router address on any router-mediated swap. No existing guard in `SwapAllowlistExtension` or the pool checks the originating EOA.

## Impact Explanation
A pool admin who configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) and also allowlists the router so those users can trade conveniently will inadvertently open the pool to all users. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and the extension will see `sender = router`, which passes the allowlist check. The curated pool's access control is silently nullified, allowing unauthorized parties to trade against LP funds under terms the pool admin did not intend. This constitutes a broken core pool functionality (access control) causing potential loss of funds to LPs who deposited under the assumption of a restricted pool.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who deploys a curated pool and wants their allowlisted users to use the standard router will encounter this. Allowlisting the router is a natural operational step. The bypass requires no special privileges — any EOA can call the router. The condition (router allowlisted + non-allowlisted user) is a foreseeable and common operational configuration.

## Recommendation
The `beforeSwap` hook must gate on an identity that survives router intermediation. Options:

1. **Pass the economic actor explicitly**: Have the router encode the originating user in `extensionData`, and have the extension decode and verify it when `sender` is a known trusted router.
2. **Introduce a `swapper` field**: Analogous to `owner` in `addLiquidity`, introduce a dedicated `swapper` parameter in the swap path that the pool passes through as the economic actor, separate from the operator/router `sender`.
3. **Document incompatibility and enforce it**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert if `sender` is a known router address.

## Proof of Concept
```
1. Pool admin deploys a pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — alice is the only allowed swapper.
3. Pool admin calls setAllowedToSwap(pool, router, true) — router allowlisted so alice can use it.
4. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, recipient: bob, ...}).
5. Router calls pool.swap(recipient=bob, ...) — router is msg.sender.
6. Pool calls extension.beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes on the curated pool despite not being allowlisted.
```
A Foundry integration test can reproduce this by deploying a pool with `SwapAllowlistExtension`, allowlisting only `alice` and the router, then asserting that a `vm.prank(bob)` call to `router.exactInputSingle` succeeds rather than reverting with `NotAllowedToSwap`.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
