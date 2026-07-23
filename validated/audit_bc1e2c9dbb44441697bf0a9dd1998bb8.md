Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address as `sender`, allowing any user to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` collapses to the router address. A pool admin who allowlists the router to enable router-based swaps for their curated user set inadvertently opens the pool to all users, as any EOA can call through the router and pass the allowlist check.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the first argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [2](#0-1) 

When any user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` — so `sender` seen by the extension is the router address, not the originating user: [3](#0-2) 

This creates an irreconcilable dilemma: if the router is not allowlisted, allowlisted users cannot use the router at all (their swap reverts because `sender = router` is not in the allowlist). If the router is allowlisted, every non-allowlisted user can bypass the per-user restriction by routing through the router.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the economic actor), not `sender` (the operator/adder contract): [4](#0-3) 

The swap path has no equivalent `owner`/`sender` separation — `sender` is the only identity available, and it collapses to the router address on any router-mediated swap.

## Impact Explanation
A pool admin who configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) and also allowlists the router so those users can trade conveniently will inadvertently open the pool to all users. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`, and the extension will see `sender = router`, which passes the check. The curated pool's access control is silently nullified, allowing unauthorized parties to trade against LP funds under terms the pool admin did not intend. This constitutes a broken core pool functionality causing potential loss of funds and an admin-boundary break where per-user swap restrictions configured by the pool admin are bypassed by an unprivileged path.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who deploys a curated pool and wants their allowlisted users to use the standard router will encounter this. Allowlisting the router is a natural operational step, not an exotic configuration. The bypass requires no special privileges — any EOA can call the router directly.

## Recommendation
The `beforeSwap` hook should gate on an identity that survives router intermediation:

1. **Align with the deposit pattern**: Introduce a `swapper` field in the swap path (analogous to `owner` in `addLiquidity`) that the pool passes through as the economic actor, separate from the operator/router `sender`. The extension would then check this field.
2. **Pass the economic actor via `extensionData`**: Have the router encode the originating user in `extensionData`, and have the extension decode and verify it — requiring that `sender` is a trusted router when this field is present.
3. **Document incompatibility**: At minimum, document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this by reverting if `sender` is a known router address.

## Proof of Concept
```
1. Pool admin deploys a pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — alice is the only allowed swapper.
3. Pool admin calls setAllowedToSwap(pool, router, true) — router is allowlisted so alice can use it.
4. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(recipient=bob, ...) — msg.sender to pool is the router.
6. Pool calls _beforeSwap(sender=router, ...) → extension.beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes on the curated pool despite not being allowlisted.
```

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
