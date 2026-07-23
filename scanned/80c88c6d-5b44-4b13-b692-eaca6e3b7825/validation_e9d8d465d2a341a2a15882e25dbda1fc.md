The call chain is fully traceable. Here is the analysis:

**Exact flow:**

1. Attacker (non-allowlisted EOA) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(...)` — at this point `msg.sender` to the pool is the **router address**.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` — passing the **router** as `sender`.
4. `ExtensionCalling._beforeSwap` encodes and forwards `sender = router` to `SwapAllowlistExtension.beforeSwap`.
5. `SwapAllowlistExtension.beforeSwap` checks: `!allowedSwapper[msg.sender][sender]` = `!allowedSwapper[pool][router]`.

If `allowedSwapper[pool][router] = true`, the check passes for **every user** who routes through the router, regardless of whether the actual EOA is allowlisted.

This is a real bypass. The extension is designed to gate individual swappers, but it only sees the intermediate router address, not the originating EOA.

---

### Title
SwapAllowlistExtension Allowlist Bypassed via Router — Any EOA Can Swap Through an Allowlisted Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the originating EOA. If the router is allowlisted, every user who calls through it bypasses the per-address allowlist.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The router is a public, permissionless contract — any EOA can call it. If a pool admin allowlists the router address (a natural setup for a "public router, restricted pool" configuration), every EOA on the network can swap, defeating the allowlist entirely.

### Impact Explanation
Pool admins use `SwapAllowlistExtension` to restrict which addresses may trade in their pool (e.g., KYC'd addresses, protocol-owned addresses, or whitelisted market makers). If the router is allowlisted, the restriction is nullified for all users. Non-allowlisted EOAs can execute swaps, moving LP funds at arbitrary prices, which constitutes direct LP-fund exposure from disallowed swappers executing trades.

### Likelihood Explanation
The `MetricOmmSimpleRouter` is the canonical swap entry point. Pool admins who want to allow "normal" swaps but restrict direct pool access will naturally allowlist the router. The bypass requires no special privileges — any EOA can call `exactInputSingle`.

### Recommendation
The extension should check `tx.origin` or, preferably, require the pool to pass the original initiator through a separate field. Alternatively, the extension documentation must explicitly warn that allowlisting a router grants access to all users of that router, and pool admins should never allowlist a public router if per-user gating is intended. A more robust fix is to have the router pass the originating `msg.sender` in `extensionData`, and have `SwapAllowlistExtension` decode and check that value when present.

### Proof of Concept
```solidity
// Foundry integration test sketch
function test_allowlistBypass_viaRouter() public {
    // Setup: pool with SwapAllowlistExtension, only router is allowlisted
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // attacker is NOT allowlisted
    assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker));

    // Attacker calls through the router
    vm.prank(attacker);
    // Should revert NotAllowedToSwap — but it succeeds
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));
    // Swap succeeds — allowlist invariant violated
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
