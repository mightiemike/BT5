Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End-User, Allowing Non-Allowlisted Users to Bypass Swap Gate via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` sets to `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router is allowlisted (required for normal router-based swaps), every user — including those explicitly excluded — can bypass the per-user swap restriction by routing through the router.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` in `ExtensionCalling` forwards that value verbatim to the extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no end-user identity forwarded: [4](#0-3) 

From the pool's perspective, `msg.sender` is the router, so the extension evaluates `allowedSwapper[pool][router]` — the router's allowlist entry — not the actual end user's entry. There is no parameter in the `swap()` call path that carries the originating user's identity to the extension. The allowlist storage is keyed `pool => swapper => bool`: [5](#0-4) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position beneficiary) rather than `sender` (the operator), because the liquidity hook receives both as distinct parameters: [6](#0-5) 

The swap hook has no equivalent `owner`/`swapper` parameter — only `sender` (immediate caller) and `recipient` (output receiver). No existing guard in the extension or pool checks the originating user's identity.

## Impact Explanation
Any user excluded from the swap allowlist can bypass the restriction by calling `MetricOmmSimpleRouter` instead of `pool.swap()` directly, provided the router is allowlisted on that pool. The intended per-user access control is rendered entirely ineffective for all router-mediated swaps. Pools configured with `SwapAllowlistExtension` for regulatory or KYC-gating purposes silently admit unrestricted users through the router path, breaking the core invariant that only allowlisted addresses may swap. This constitutes a broken core pool access-control mechanism reachable by any unprivileged user.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard user-facing swap entry point. A pool admin who wants to support normal user flows must allowlist the router. The bypass is therefore reachable in any production deployment where the router is allowlisted and per-user restrictions are also intended — a common and natural configuration. No special privileges or malicious setup are required; any user can call the router directly.

## Recommendation
Pass the originating user's address through the swap call chain so the extension can check it. The cleanest fix mirrors the `addLiquidity` / `DepositAllowlistExtension` pattern: add an explicit `swapper` parameter to `pool.swap()` (analogous to `owner` in `addLiquidity`). The extension then checks `allowedSwapper[pool][swapper]` instead of `allowedSwapper[pool][sender]`. `MetricOmmSimpleRouter` would forward `msg.sender` as `swapper`. Alternatively, require the router to encode the actual user identity in `extensionData` and have the extension decode and verify it, though this is more complex and requires router cooperation.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as `beforeSwap` hook.
2. Pool admin sets `allowAllSwappers = false` (default deny).
3. Pool admin calls `setAllowedToSwap(pool, address(router), true)` so normal router-based swaps work.
4. Pool admin does **not** call `setAllowedToSwap(pool, address(alice), true)` — Alice is a restricted user.
5. Alice calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
6. The router calls `IMetricOmmPoolActions(pool).swap(recipient, ...)` with `msg.sender = router`.
7. The pool calls `_beforeSwap(router, recipient, ...)`.
8. The extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
9. Alice's swap executes successfully despite not being individually allowlisted.

**Expected:** Alice's swap reverts with `NotAllowedToSwap`.
**Actual:** Alice's swap succeeds because the router's allowlist entry is checked, not Alice's.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
