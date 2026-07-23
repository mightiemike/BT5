Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Any User to Bypass the Curated-Pool Allowlist - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. A pool admin who allowlists the router (the only way to permit any router-mediated swap on a curated pool) simultaneously grants unrestricted access to every address on the network.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key and `sender` (the direct pool caller) as the identity being checked: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [3](#0-2) 

The same pattern holds for `exactInput`: [4](#0-3) 

And for `exactOutputSingle` and `exactOutput`, and the recursive callback path in `_exactOutputIterateCallback`: [5](#0-4) 

In every router-mediated swap, the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. The router stores the real payer in transient storage via `_setNextCallbackContext` but never surfaces it to the extension layer. There is no existing guard that recovers the originating user identity before the allowlist check executes.

## Impact Explanation

A curated pool protected by `SwapAllowlistExtension` is designed to restrict trading to a specific set of addresses. Once the router is allowlisted (the only operational configuration that permits router-mediated swaps), the guard returns `IMetricOmmExtensions.beforeSwap.selector` for every caller regardless of individual allowlist status. Non-allowlisted users can execute swaps against a pool explicitly closed to them, draining LP inventory at oracle prices or executing arbitrage. This constitutes a direct loss of the pool's curation guarantee and, depending on pool composition, a direct loss of LP principal through unwanted trades. This is a High severity impact: broken core pool functionality causing loss of funds and unusable access-control flows.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who wants any allowlisted user to swap through the router must allowlist the router address, because the extension only sees the router as `sender`. This is the natural, expected configuration. The bypass therefore activates under normal operational setup, not under adversarial or exotic conditions. Any unprivileged user can exploit this by calling any of the four router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

## Recommendation

The extension must check the economically relevant actor, not the intermediary. The cleanest fix: require callers of `pool.swap()` to attest the originating user via `extensionData`. The extension decodes the real user from `extensionData` when `sender` is a known router, and checks `sender` directly otherwise. Alternatively, the router can write the originating user to a known transient storage slot that the extension reads directly, avoiding any core changes. A registry of trusted routers maintained by the pool admin would gate which `sender` values trigger the `extensionData` decode path.

## Proof of Concept

```solidity
// Setup: pool admin creates a curated pool with SwapAllowlistExtension
// and allowlists the router so that router-mediated swaps are possible.
swapAllowlist.setAllowedToSwap(pool, address(router), true);
// alice is NOT individually allowlisted — allowedSwapper[pool][alice] == false

// Attack: alice bypasses the allowlist by going through the router
vm.prank(alice);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        recipient: alice,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// pool.swap() is called with msg.sender == router
// _beforeSwap(router, ...) is forwarded to the extension
// extension checks allowedSwapper[pool][router] == true → passes
// alice receives token1 from a pool she was never authorized to trade on
```

The extension checks `allowedSwapper[pool][router]` (true) instead of `allowedSwapper[pool][alice]` (false), so the guard passes and the swap executes. [6](#0-5) [3](#0-2)

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
