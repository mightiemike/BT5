### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass Per-User Swap Allowlist via Allowlisted Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps using the `sender` argument, which the pool always sets to `msg.sender` of `pool.swap`. When users route through `MetricOmmSimpleRouter`, `msg.sender` is the router contract, not the end user. If the router is allowlisted, every user of the router bypasses the per-user allowlist entirely.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
              → MetricOmmPool._beforeSwap(msg.sender, ...)    // sender = router
                   → ExtensionCalling._beforeSwap(sender=router, ...)
                        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` (the direct caller) as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is the router. The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` directly — there is no mechanism to forward the original `msg.sender` (the end user) to the pool: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

A pool admin who wants to allow normal trading through the official router while restricting direct pool access to specific users has no way to do so. Allowlisting the router (`allowedSwapper[pool][router] = true`) grants every user of the router unrestricted swap access, completely defeating the per-user curation mechanism. Any non-allowlisted user can execute swaps on a curated pool by routing through the allowlisted router and receive output tokens. This breaks the core pool curation functionality that `SwapAllowlistExtension` is designed to enforce.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap interface. Pool admins who deploy curated pools with `SwapAllowlistExtension` and also want to support router-mediated swaps (a common and expected configuration) will inevitably allowlist the router, triggering this bypass for all users. The path is fully permissionless once the router is allowlisted.

---

### Recommendation

Pass the original end-user address through the call chain. One approach: add an optional `payer` or `originator` field to the swap parameters or `extensionData` that the router populates with `msg.sender`, and have `SwapAllowlistExtension` read it. Alternatively, the extension should check `recipient` or require the pool to expose the original initiator. The simplest fix is for the router to encode `msg.sender` into `extensionData` and for `SwapAllowlistExtension` to decode and check it instead of (or in addition to) `sender`.

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_nonAllowlistedUserBypassesViaRouter() public {
    // Pool admin allowlists the router (common setup for public trading)
    vm.prank(poolAdmin);
    swapAllowlistExtension.setAllowedToSwap(address(pool), address(router), true);

    // attacker is NOT allowlisted
    assertFalse(swapAllowlistExtension.isAllowedToSwap(address(pool), attacker));

    // attacker routes through the allowlisted router
    vm.prank(attacker);
    token1.approve(address(router), type(uint256).max);

    uint256 outBefore = token0.balanceOf(attacker);
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token1),
        recipient: attacker,
        amountIn: 1000,
        amountOutMinimum: 0,
        zeroForOne: false,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp,
        extensionData: ""
    }));

    // Non-allowlisted attacker received output tokens — allowlist bypassed
    assertGt(token0.balanceOf(attacker), outBefore);
}
```

The swap succeeds because `beforeSwap` receives `sender = address(router)`, which is allowlisted, while the actual end user (`attacker`) is never checked. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
