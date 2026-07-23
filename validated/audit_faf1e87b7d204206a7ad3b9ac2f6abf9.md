Audit Report

## Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the direct caller of the pool. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` at the pool, not the actual end user. A pool admin who allowlists the router to support router-mediated swaps for curated users inadvertently opens the pool to every user, completely defeating the allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of the pool: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router `msg.sender` at the pool: [3](#0-2) 

For multi-hop `exactInput`, hops after the first use `address(this)` (the router) as payer and the router calls each pool directly: [4](#0-3) 

For `exactOutput`, the recursive callback path also calls the next pool with `msg.sender = router`: [5](#0-4) 

In all three router paths, the extension sees `sender = router`, not the actual end user. The pool admin faces an impossible choice: do not allowlist the router (allowlisted users cannot use the standard router) or allowlist the router (every user on the network can bypass the allowlist). By contrast, `DepositAllowlistExtension` correctly checks `owner` — the position owner explicitly passed by the caller — rather than `sender`: [6](#0-5) 

## Impact Explanation
A curated pool restricting swaps to specific counterparties (KYC'd users, whitelisted market makers, etc.) is completely open to any user the moment the pool admin allowlists the router. Every token in the pool's bins is exposed to unauthorized swaps at oracle-derived prices, including draining the pool of one token leg. This is a direct loss of LP principal and a broken core pool invariant — the allowlist extension is rendered entirely ineffective for its stated purpose when the router is allowlisted.

## Likelihood Explanation
The router is the standard, documented entry point for swaps. Any pool admin who wants their allowlisted users to interact via the router must allowlist it. The admin has no way to simultaneously allow router-mediated swaps for curated users and block non-curated users, because the router does not forward the original caller's identity. The bypass requires no special privileges — any user with knowledge of the pool address can exploit it by calling `exactInputSingle`, `exactInput`, or `exactOutput` on the router.

## Recommendation
The `sender` argument forwarded to extensions should reflect the economic actor, not the intermediary. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: store the original `msg.sender` in transient storage alongside the payer context and expose it via `extensionData` so extensions can read the true initiator.
2. **In `SwapAllowlistExtension`**: decode the true initiator from `extensionData` (a caller-supplied, router-populated field) rather than relying on the `sender` argument, which is always the direct pool caller.

Alternatively, document clearly that `sender` is the direct pool caller and that allowlisting the router opens the pool to all users — but this makes the extension unsuitable for its stated purpose of curating access.

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Pool admin allowlists Alice (a legitimate counterparty):
       swapAllowlist.setAllowedToSwap(pool, alice, true)
3. Alice tries to use the router and gets blocked (router not allowlisted).
4. Pool admin allowlists the router to fix Alice's UX:
       swapAllowlist.setAllowedToSwap(pool, router, true)
5. Bob (not allowlisted, arbitrary user) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
6. Router calls pool.swap(...) with msg.sender = router.
7. Extension checks allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes at oracle price, draining the pool of the output token.
   Alice's curated pool is now open to the entire public.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
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
