Audit Report

## Title
`SwapAllowlistExtension` checks the router's address as `sender` instead of the actual user, allowing any unprivileged address to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router — the necessary step for any allowlisted user to use the standard swap interface — every address can bypass the per-user allowlist by routing through the router. No configuration simultaneously supports router-mediated swaps and enforces per-user allowlisting.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [3](#0-2) 

The same substitution occurs in `exactInput` (multi-hop): [4](#0-3) 

And in `exactOutputSingle`: [5](#0-4) 

The exploit path is:
1. Pool admin deploys pool with `SwapAllowlistExtension`, allowlists `alice` as a swapper.
2. Admin also allowlists the router so `alice` can use the standard interface: `allowedSwapper[pool][router] = true`.
3. `bob` (not allowlisted) calls `router.exactInputSingle(...)`.
4. The router calls `pool.swap(...)` — pool sees `msg.sender = router`.
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true` → passes.
6. Bob's swap executes despite never being allowlisted.

The existing guard in `beforeSwap` is structurally insufficient: it checks the intermediary, not the originating user. There is no mechanism in the current code to recover the original `msg.sender` from the router.

## Impact Explanation

A pool deployer uses `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., a KYC-gated or institutional pool). Once the router is allowlisted — which is required for any allowlisted user to trade via the standard interface — the allowlist is silently bypassed by any EOA. The attacker receives output tokens from the pool and the callback pulls input tokens from the attacker's approved balance. LP funds are exposed to unauthorized trades that the allowlist was specifically configured to prevent. This constitutes a broken core pool access-control invariant with direct fund impact on LP assets.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who wants to support normal user flows will allowlist the router as a matter of course. The bypass requires no special privilege, no flash loan, and no multi-block setup: a single `exactInputSingle` call from any EOA suffices once the router is allowlisted. The condition is trivially reachable in any production deployment that combines `SwapAllowlistExtension` with the standard router.

## Recommendation

The extension must gate the end user, not the intermediary. Two sound approaches:

1. **Pass the original caller through the router.** Have the router encode the original `msg.sender` in `extensionData` and have the extension decode and check it. This requires a trust assumption that the router is the only allowed intermediary.

2. **Add a `realSender` field to extension data.** Require the router to always populate a dedicated ABI field with its own `msg.sender`, and have the extension prefer that field over the raw `sender` when the raw `sender` is a known router.

3. **Simplest fix:** Document and enforce that when `SwapAllowlistExtension` is active, users must call the pool directly; the router must never be allowlisted as a swapper.

## Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only alice is allowlisted.
// Admin also allowlists the router so alice can use it.
swapExtension.setAllowedToSwap(address(pool), alice, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attack: bob (not allowlisted) swaps through the router.
// Extension sees sender = router (allowlisted) → passes.
vm.startPrank(bob);
token0.approve(address(router), type(uint256).max);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             address(pool),
        recipient:        bob,
        tokenIn:          address(token0),
        zeroForOne:       true,
        amountIn:         1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:    0,
        deadline:         block.timestamp + 1,
        extensionData:    ""
    })
);
// Bob receives token1 despite never being allowlisted.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-137)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
