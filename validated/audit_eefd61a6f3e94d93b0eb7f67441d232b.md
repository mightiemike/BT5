Audit Report

## Title
SwapAllowlistExtension checks router address instead of end user, allowing allowlist bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's `msg.sender` — i.e., whoever called `pool.swap()`. When `MetricOmmSimpleRouter` intermediates the call, `sender` equals the router address, not the end user. The allowlist check is therefore applied to the router contract rather than the actual swapper, making the guard bypassable for any user who calls through the router.

## Finding Description
In `MetricOmmPool.swap`, the pool calls `_beforeSwap(msg.sender, recipient, ...)` at line 230–240, passing its own `msg.sender` as `sender`. [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` value directly to the extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`. [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract address. [4](#0-3) 

The allowlist lookup therefore becomes `allowedSwapper[pool][router]` — the end user (`msg.sender` of the router call) is never checked. By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks `owner`, an explicit parameter supplied by the caller that represents the actual position owner regardless of intermediary. [5](#0-4) 

There is no existing guard in `SwapAllowlistExtension` that recovers the true end-user identity from `extensionData` or any other source.

## Impact Explanation
Two fund-impacting failure modes arise from this structural mismatch:

1. **Allowlist bypass (unauthorized swaps):** If the pool admin allowlists the router (the natural operational step to enable standard UX), every user — including those explicitly denied — can swap freely through the router. The allowlist guard is completely defeated for the router path, breaking the core invariant that the allowlist controls which addresses may trade in a restricted pool.

2. **Allowlist DoS (authorized users locked out):** If the pool admin does not allowlist the router, all individually allowlisted users are unable to swap through `MetricOmmSimpleRouter` even though they hold explicit permission, forcing direct `pool.swap()` calls that may not be the intended UX.

Both outcomes constitute broken core pool functionality and an admin-boundary break: the pool admin's access control configuration is rendered ineffective by an unprivileged path.

## Likelihood Explanation
No special preconditions are required beyond the standard operational setup. Any user can call `MetricOmmSimpleRouter` without privilege. Pool admins who configure `SwapAllowlistExtension` will naturally expect it to gate end users, not the router contract. Allowlisting the router is the expected operational state to enable normal swap UX, making the bypass condition the default rather than an edge case.

## Recommendation
Pass the actual end user through the extension data or add a dedicated `swapper` parameter to the `beforeSwap` hook that the pool populates from a user-supplied field, analogous to how `owner` is an explicit parameter in `beforeAddLiquidity`. Alternatively, the router should encode `msg.sender` into `extensionData` and `SwapAllowlistExtension` should decode it, or the extension should check the `recipient` argument if that reliably represents the end user in all swap paths.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` as `extension1` and `beforeSwap` order set.
2. Admin calls `setAllowedToSwap(pool, router, true)` to allow the router (standard operational step).
3. Admin does not allowlist Alice (or explicitly sets `allowedSwapper[pool][alice] = false`).
4. Alice calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router address.
6. Pool calls `_beforeSwap(router, ...)` → extension receives `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Alice's swap executes despite being explicitly denied, as confirmed by the call chain in `MetricOmmPool.swap` (line 230) and `SwapAllowlistExtension.beforeSwap` (line 37). [6](#0-5) [7](#0-6)

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
