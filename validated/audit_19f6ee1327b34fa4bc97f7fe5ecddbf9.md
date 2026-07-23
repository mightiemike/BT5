Audit Report

## Title
SwapAllowlistExtension checks router address instead of originating EOA, enabling allowlist bypass for any EOA via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which `MetricOmmPool.swap` binds to `msg.sender` — the immediate caller of the pool. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating EOA. If the pool admin allowlists the router (the natural configuration for router-mediated pools), every EOA — including explicitly non-allowlisted ones — can bypass the restriction by calling the router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` value directly into the encoded extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` stores the originating EOA only in transient callback context for payment, but calls `pool.swap(...)` directly from the router — so the pool always sees `msg.sender = router`: [4](#0-3) 

The check resolves to `allowedSwapper[pool][router]`. If the router is allowlisted, the gate passes for **any** EOA that calls through the router, regardless of whether that EOA is individually allowlisted. There is no configuration that simultaneously allows router-mediated swaps and correctly gates individual end-users.

## Impact Explanation

The allowlist is the sole access-control mechanism for curated pools (e.g., institutional-only, KYC-gated, or whitelist-only pools). Any EOA can trade on a restricted pool by routing through `MetricOmmSimpleRouter` if the router is allowlisted. This constitutes broken core pool functionality and a direct admin-boundary break: the pool admin's access restriction is rendered ineffective by an unprivileged actor using the standard swap entry point.

## Likelihood Explanation

The router is the standard, documented swap entry point for end-users. A pool admin configuring a curated pool with `SwapAllowlistExtension` would naturally allowlist the router to permit router-mediated swaps for their approved users. This is the expected operational pattern, making the misconfiguration highly likely in practice. No privileged access or special conditions are required — any EOA can call `exactInputSingle` on the router.

## Recommendation

The `sender` forwarded to extension hooks should reflect the originating user, not the immediate pool caller. The cleanest fix: have `MetricOmmSimpleRouter` encode the originating EOA in `extensionData`, and have `SwapAllowlistExtension` decode and verify it when `msg.sender` (the pool's caller) is a known trusted router, falling back to `sender` for direct calls. This requires the pool to maintain a trusted-router registry or the extension to verify the forwarded identity via a signed/encoded proof.

## Proof of Concept

```solidity
function test_allowlistBypass_viaRouter() public {
    // Setup: deploy pool with SwapAllowlistExtension
    // Admin allowlists ONLY the router (not the attacker EOA)
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    address attacker = makeAddr("attacker");
    assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker));

    token0.mint(attacker, 1_000e18);
    vm.prank(attacker);
    token0.approve(address(router), type(uint256).max);

    // Attacker routes through the router — swap succeeds despite not being allowlisted
    vm.prank(attacker);
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1_000,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));
    // Swap succeeds — allowlist bypassed
    // pool._beforeSwap received sender = address(router), which is allowlisted
}
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
