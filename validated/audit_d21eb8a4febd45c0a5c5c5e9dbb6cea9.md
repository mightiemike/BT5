Audit Report

## Title
`SwapAllowlistExtension` gates the direct caller of `pool.swap()` rather than the originating user, enabling allowlist bypass or lockout for router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `sender` equals the router contract address — not the originating user. This produces two concrete failure modes: (1) if the pool admin allowlists the router address to enable router-mediated swaps, every user — including those the allowlist was meant to exclude — can bypass the per-user gate; (2) if the admin allowlists specific user addresses, those users cannot swap via the router because the router address is not allowlisted, making the extension unusable with the primary swap interface.

## Finding Description

**Root cause — `MetricOmmPool.swap` passes `msg.sender` as `sender`:** [1](#0-0) 

`_beforeSwap` in `ExtensionCalling` forwards `sender` unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed by `[pool][sender]`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` to the pool — the originating user's address is never forwarded: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) [6](#0-5) 

**Structural asymmetry with `DepositAllowlistExtension`:** The deposit extension correctly gates `owner` — the economic beneficiary passed explicitly as a separate argument — rather than `msg.sender` of `addLiquidity`: [7](#0-6) 

No equivalent separation exists for swaps. The `beforeSwap` signature receives `sender` (the direct caller) and `recipient` (the output recipient), but neither is the originating user when the router intermediates.

**Existing guards are insufficient:** `allowedSwapper` is keyed on `[pool][sender]`. There is no mechanism in the pool or router to forward the originating user's address as `sender`. The check `allowAllSwappers[msg.sender]` only bypasses the per-user gate entirely, which does not help the targeted allowlist use case.

## Impact Explanation

Two fund-impacting outcomes:

1. **Allowlist bypass (unauthorized access):** A pool admin allowlists the router address so that their allowlisted users can swap via the router. Because `allowedSwapper[pool][router] = true`, every user — including those the admin intended to exclude — can call `MetricOmmSimpleRouter` and pass the gate. Unauthorized traders execute swaps on a pool designed to be restricted (e.g., institutional-only, KYC-gated), causing adverse price impact that harms LP principal and enabling arbitrage that drains pool value. This is a direct loss of LP assets, meeting the Critical/High threshold.

2. **Allowlist lockout (broken core functionality):** A pool admin allowlists specific user addresses. Those users call the router. The extension checks `allowedSwapper[pool][router]`, which is `false`, so the swap reverts with `NotAllowedToSwap` even for legitimately allowlisted users. The allowlist becomes unusable with the primary swap interface, constituting broken core swap functionality.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap interface; most users route through it rather than calling the pool directly.
- Any user can call the router — it is a public, permissionless contract.
- The bypass is triggered the moment a pool admin allowlists the router address, which is the natural corrective action when allowlisted users report that router swaps are failing (failure mode 2 leads directly to failure mode 1).
- No special privilege, flash loan, or non-standard token behavior is required.

## Recommendation

Pass the originating user's address through the router as an explicit `swapper` parameter, analogous to how `owner` is passed for deposits. One approach: add a `swapper` field to the router's swap parameter structs and forward it as the `sender` argument to `pool.swap()`. The pool would then pass this explicit swapper address to `_beforeSwap` instead of `msg.sender`. Alternatively, document clearly that `SwapAllowlistExtension` gates the direct caller of `pool.swap()` and that per-user gating is impossible through the router — but this effectively removes the extension's utility for router-mediated pools.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on the `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — intending to enable router-mediated swaps for their allowlisted users.
3. Attacker (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the restricted pool.
4. The router calls `pool.swap(...)` with `msg.sender = router`.
5. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender=router, ...)`.
6. `SwapAllowlistExtension.beforeSwap` receives `sender = router`; checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Attacker successfully swaps on a pool they were never individually authorized to access, causing adverse price impact on LP positions.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-165)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
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
