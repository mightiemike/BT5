Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing any router user to bypass per-user swap restrictions — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool` populates with `msg.sender` — the router address when a swap is routed through `MetricOmmSimpleRouter`. If a pool admin allowlists the router to enable router-mediated swaps for individually approved users, every router user bypasses the per-user restriction. `DepositAllowlistExtension` avoids this by checking the explicit `owner` parameter, which `MetricOmmPoolLiquidityAdder` preserves end-to-end; no equivalent parameter exists on the swap path.

## Finding Description

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, passing the direct caller as `sender`: [1](#0-0) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making `msg.sender` inside the pool the router address: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`: [3](#0-2) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the second parameter), which is the economically relevant party: [4](#0-3) 

This works correctly because `MetricOmmPoolLiquidityAdder` explicitly passes `positionOwner` to `addLiquidity`: [5](#0-4) 

The swap function has no equivalent explicit `swapper` parameter, so the extension has no on-chain way to recover the originating user. The guard evaluates the wrong identity and the existing check is structurally insufficient.

## Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to KYC-verified or otherwise approved addresses, and who also allowlists the router so those approved users can trade via the router, inadvertently grants swap access to every router user. The intended access control is silently defeated: unauthorized parties execute swaps in a pool designed to be restricted, constituting broken core pool functionality (the allowlist extension's sole purpose is to gate swap access, and that gate is bypassed by an unprivileged caller).

## Likelihood Explanation

Allowlisting the router is a natural and expected operational step for any pool admin who wants approved users to trade via the standard router interface. No privileged role is required on the attacker side — any public address can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The inconsistency between how deposit and swap allowlists resolve identity makes the misconfiguration easy to overlook.

## Recommendation

1. **Preferred fix**: Have `MetricOmmSimpleRouter` encode the originating `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it, analogous to how `positionOwner` is passed explicitly through `addLiquidity`.
2. **Alternative**: Add an explicit `swapper` parameter to the pool's `swap` function (mirroring `owner` in `addLiquidity`) so extensions can gate the economically relevant party rather than the direct caller.
3. **Minimum**: Document clearly that allowlisting the router grants swap access to all router users, not just individually allowlisted addresses.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` attached to the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is individually allowlisted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — `msg.sender` inside the pool is the router.
6. Pool calls `_beforeSwap(router, ...)` — `sender` = router address.
7. Extension evaluates `allowedSwapper[pool][router]` = `true` → no revert.
8. Bob's swap executes successfully in a pool he was never individually allowlisted for, bypassing the intended access control.

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L194-196)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```
