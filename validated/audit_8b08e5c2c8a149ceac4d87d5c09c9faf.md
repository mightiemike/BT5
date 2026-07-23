Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Originating User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` sets to `msg.sender` — the immediate caller of the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract. If the router is allowlisted (required for normal router-based swaps), every user — including those explicitly excluded — can bypass the per-user restriction by routing through the router. The `DepositAllowlistExtension` avoids this exact problem by checking the explicit `owner` parameter instead of `sender`.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and all other swap entry points) calls `pool.swap()` directly with no originating-user identity forwarded: [4](#0-3) 

The pool therefore sees `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]` — the router's entry — not the actual end user's entry. There is no parameter in the `swap()` call path that carries the originating user's identity to the extension.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` entirely and checks the explicit `owner` parameter (the position beneficiary), which the router or any operator must supply explicitly: [5](#0-4) 

The `swap()` interface has no equivalent explicit beneficiary parameter, so the extension has no way to distinguish different end users behind the same router address.

## Impact Explanation
Any user excluded from the swap allowlist can bypass the restriction by calling `MetricOmmSimpleRouter` instead of `pool.swap()` directly, provided the router is allowlisted on that pool. The intended per-user access control is rendered entirely ineffective for all router-mediated swaps. Pools configured with `SwapAllowlistExtension` for regulatory or KYC-gating purposes silently admit unrestricted users through the router path, breaking the core invariant that only allowlisted addresses may swap. This is an admin-boundary break: the pool admin's access control configuration is bypassed by an unprivileged path available to any user.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard user-facing swap entry point. A pool admin who wants to support normal user flows must allowlist the router. The bypass is therefore reachable in any production deployment where the router is allowlisted and per-user restrictions are also intended — a common and natural configuration. No special privileges or malicious setup are required; any user can call the router directly.

## Recommendation
Pass the originating user's address through the swap call chain so the extension can check it. The cleanest fix mirrors the `addLiquidity` / `DepositAllowlistExtension` pattern:

1. **Add an explicit `swapper` parameter to `pool.swap()`** (analogous to `owner` in `addLiquidity`). The extension checks `allowedSwapper[pool][swapper]` instead of `allowedSwapper[pool][sender]`. The router would forward `msg.sender` as `swapper`.
2. Alternatively, **decode the originating user from `extensionData`** and require the router to forward it, but this is more complex and requires router cooperation.

Option 1 is the cleaner fix and directly mirrors the existing `addLiquidity` / `DepositAllowlistExtension` design. [5](#0-4) 

## Proof of Concept
**Setup:**
1. Deploy a pool with `SwapAllowlistExtension` configured as `beforeSwap` hook.
2. Pool admin calls `setAllowAllSwappers(pool, false)` — default deny.
3. Pool admin calls `setAllowedToSwap(pool, address(router), true)` — allowlist the router so normal router-based swaps work.
4. Pool admin does **not** call `setAllowedToSwap(pool, address(alice), true)` — Alice is a restricted user.

**Attack:**
5. Alice calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
6. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
7. The pool calls `_beforeSwap(router, recipient, ...)`.
8. The extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
9. Alice's swap executes successfully despite not being individually allowlisted.

**Expected:** Alice's swap reverts with `NotAllowedToSwap`.
**Actual:** Alice's swap succeeds because the router's allowlist entry is checked, not Alice's. [6](#0-5) [4](#0-3)

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
