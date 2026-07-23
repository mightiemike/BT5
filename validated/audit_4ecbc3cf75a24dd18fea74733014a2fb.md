Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the user. If the router is allowlisted — the only way to permit any router-mediated swap — every address, including explicitly excluded ones, can bypass the curated pool's swap allowlist by calling through the router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly, with no field carrying the original user's address: [4](#0-3) 

At that point `msg.sender` inside `pool.swap` is the router address, so the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. The actual user's allowlist status is never consulted. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

`DepositAllowlistExtension` does not share this flaw because it checks the `owner` parameter (the position owner), which the pool passes explicitly and which the liquidity adder sets to the real depositor: [5](#0-4) 

The wrong value checked is `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, causing the extension's access-control decision to be based on the intermediary contract rather than the actual trading party.

## Impact Explanation
A pool admin who deploys a pool with `SwapAllowlistExtension` on the `beforeSwap` hook to restrict swaps to a curated set of addresses must allowlist the router to permit any router-mediated swap. Once the router is allowlisted, **any** address — including those explicitly excluded — can call any of the four router swap functions and the extension will pass them through, because it only sees the router's address. The curated pool's access control is completely defeated for all router-mediated swaps. Depending on the pool's purpose (institutional-only, KYC-gated, whitelist-only), this results in unauthorized trading and direct loss of the pool's curation guarantee, constituting broken core pool functionality (access control bypass).

## Likelihood Explanation
The router is the primary supported periphery swap path. Any user who discovers the bypass can exploit it immediately with no special privileges, no malicious setup, and no non-standard tokens. The only precondition is that the pool admin has allowlisted the router, which is the only way to allow legitimate router-mediated swaps, making the bypass trivially reachable in any real deployment.

## Recommendation
Mirror the deposit allowlist pattern: have the router forward the real user's address in `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router. Alternatively, extend the pool's `swap` interface with an explicit `swapper` field (analogous to `owner` in `addLiquidity`) that the router sets to `msg.sender` before calling the pool, and have the pool pass that field — not its own `msg.sender` — to the extension as `sender`.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` on the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — required to allow any router-mediated swap.
3. Pool admin calls `setAllowedToSwap(pool, alice, false)` (or simply never allowlists Alice).
4. Alice calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. `_beforeSwap(router, ...)` is forwarded to the extension.
7. Extension checks `allowedSwapper[pool][router] == true` → passes.
8. Alice's swap executes despite being excluded from the allowlist.

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
