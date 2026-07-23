Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing full allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router is allowlisted for a pool, every user — including explicitly disallowed ones — can bypass the swap gate by routing through the router.

## Finding Description
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)`, forwarding its own `msg.sender` as the `sender` argument to every configured extension. [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes this `sender` value and dispatches it to each extension in order. [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(...)` directly, making the router the pool's `msg.sender`. [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The allowlist check therefore resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user, including those explicitly denied.

The asymmetry with `DepositAllowlistExtension` confirms the design intent: the deposit guard receives both `sender` (the caller) and `owner` (the economic actor) and correctly gates on `owner`. [6](#0-5) 

The swap allowlist applies the same pattern inconsistently, checking the intermediary instead of the end user. No existing guard in the extension or pool prevents this substitution.

## Impact Explanation
Any user denied by the swap allowlist can bypass it by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) on a pool where the router is allowlisted. The allowlist — the sole on-chain mechanism for restricting swap access — is rendered ineffective. Pools designed for permissioned trading (e.g., institutional or KYC-gated pools) accept swaps from arbitrary addresses, directly violating the LP-facing access guarantee and potentially exposing LP assets to unauthorized counterparties. This constitutes a broken core pool functionality causing loss of the access-control guarantee and an admin-boundary break where the pool admin's per-user restrictions are bypassed by an unprivileged path.

## Likelihood Explanation
The trigger condition is that the router is allowlisted for the pool. This is the expected operational state for any pool that wants to support the standard periphery UX. A pool admin who enables router access while believing individual-user restrictions still apply will unknowingly create the bypass. The exploit requires no special privileges — any public user can call the router.

## Recommendation
Gate on the actual end-user identity rather than the intermediary. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router convention.
2. **Mirror the deposit allowlist pattern**: Introduce a separate `swapper` identity field analogous to `owner` in `beforeAddLiquidity`, populated by the pool from a caller-supplied parameter rather than from `msg.sender`. The pool would need a protocol-level change to carry the originating user address.

The simplest safe default is to document that `allowedSwapper[pool][router]` grants access to all router users, and require pool admins to use `allowAllSwappers = false` with no router entry if individual-user gating is intended — but this breaks router usability entirely. A proper fix requires surfacing the real user identity to the extension.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin calls `setAllowedToSwap(pool, alice, false)` (or simply never allowlists Alice).
4. Alice calls `router.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Alice successfully swaps despite being explicitly excluded from the allowlist.

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
