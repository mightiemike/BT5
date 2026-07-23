Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router is allowlisted — a natural admin configuration to support periphery routing — every user, including those explicitly excluded, can bypass the swap allowlist by routing through the router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` directly to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` is called, the router calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The real user's address is stored only in transient storage via `_setNextCallbackContext` for the payment callback and is never surfaced to extensions. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The result: `SwapAllowlistExtension` evaluates `allowedSwapper[pool][router]` — the router's address — rather than the actual end user's address.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the LP position owner, the economically relevant actor), not `sender`: [6](#0-5) 

The swap extension has no equivalent correct-actor binding.

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to specific counterparties faces two bad outcomes:

1. **Allowlist bypass (High):** If the admin allowlists the router (the natural configuration to support the supported periphery path), every user — including those explicitly excluded — can bypass the gate by calling `MetricOmmSimpleRouter`. Disallowed users trade on a pool designed to exclude them, violating the pool's curation invariant and potentially draining LP assets at prices the pool was not intended to offer to arbitrary counterparties.

2. **Broken functionality (Medium):** If the admin does not allowlist the router, allowlisted users cannot use the supported periphery path at all, breaking core swap functionality for legitimate users.

The first outcome is the more severe: it is a direct, unprivileged bypass of a live access-control guard, causing direct loss of LP assets to unauthorized counterparties.

## Likelihood Explanation

The trigger requires the router to be allowlisted, which is a natural and expected admin action — the router is the protocol's own supported swap entry point. A pool admin who wants to support both curated access and router-mediated swaps will allowlist the router, unknowingly opening the gate to all users. The attacker needs no special role; any address can call `MetricOmmSimpleRouter.exactInputSingle`.

## Recommendation

The extension must check the actual end user, not the intermediary. Two options:

1. **Pass the original caller through the swap call.** The pool's `swap` function should accept an explicit `originator` parameter (or read it from transient storage set by the router) and pass it as `sender` to extensions, so the extension sees the real user.

2. **Have the extension read the payer from transient storage.** Since the router already stores the real payer via `_setNextCallbackContext`, the extension could read that slot directly. This is tightly coupled but avoids changing the pool interface.

The `DepositAllowlistExtension` pattern (gating on `owner`, the economic actor) should be the model: the swap allowlist must gate on the address that economically benefits from the swap, not the address that mechanically calls the pool.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as `beforeSwap` hook.
2. Admin calls `setAllowedToSwap(pool, address(router), true)` — a natural step to support periphery routing.
3. Attacker (`alice`, not on the allowlist) calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. Swap executes. Alice, who was never allowlisted, has traded on the curated pool.

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
