Audit Report

## Title
`SwapAllowlistExtension` gates on the router address instead of the actual user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router contract address. Any pool admin who adds the router to the allowlist to enable router-based swaps inadvertently grants every user the ability to bypass the per-address restriction, rendering the allowlist ineffective for all router-based swap flows.

## Finding Description

`SwapAllowlistExtension.beforeSwap` gates on `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

`msg.sender` here is the pool (enforced by `onlyPool` in `BaseMetricExtension`). `sender` is the first argument forwarded by `MetricOmmPool.swap` via `_beforeSwap(msg.sender, ...)`: [2](#0-1) 

So `sender` = `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap`: [3](#0-2) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The same issue applies to multi-hop `exactInput` (L103-112) and `exactOutput` paths. [4](#0-3) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly keys on `owner` (the economic actor, second argument), not `sender` (the intermediary): [5](#0-4) 

For a curated pool to support router-based swaps at all, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, every user — regardless of individual allowlist status — can bypass the restriction by routing through `MetricOmmSimpleRouter`. There is no way to simultaneously allow router-based swaps and enforce per-user restrictions with the current design.

## Impact Explanation

The swap allowlist is the sole access-control mechanism for curated pools (e.g., institutional, KYC-gated, or regulatory-compliant pools). Once the router is added to the allowlist — the natural and expected configuration for any curated pool that also wants to support the protocol's own router — the allowlist is rendered entirely ineffective for all router-based swap flows. Any unprivileged user can bypass the restriction by routing through `MetricOmmSimpleRouter`. This constitutes broken core pool functionality and an admin-boundary break reachable by an unprivileged trader.

## Likelihood Explanation

The trigger is the pool admin adding the router to the allowlist, which is the natural and expected configuration for any curated pool that also wants to support the protocol's own router. There is no way to simultaneously allow router-based swaps and enforce per-user restrictions with the current extension design, so any pool admin who attempts to do so will unknowingly open the bypass. The bypass is repeatable by any user with no special privileges beyond access to the router.

## Recommendation

The extension must check the actual economic actor, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.
2. **Use `extensionData` to carry the originating user address:** Gate on that field when `sender` is a known router, falling back to `sender` for direct callers.

The `DepositAllowlistExtension` pattern (keying on `owner`, the economic actor) should be the model: the swap extension should analogously key on the actual user initiating the swap, not the intermediary contract.

## Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Pool admin: setAllowedToSwap(pool, alice, true)   // Alice is the only allowed swapper
3. Pool admin: setAllowedToSwap(pool, router, true)  // Enable router-based swaps
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(...) — msg.sender = router
6. Pool calls _beforeSwap(router, ...) — sender = router
7. Extension checks allowedSwapper[pool][router] == true → passes
8. Bob's swap executes successfully despite not being individually allowlisted.
```

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
