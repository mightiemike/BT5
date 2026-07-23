### Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument the pool passes to the extension. The pool always sets `sender = msg.sender` of the `swap()` call. When users route through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router**, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, producing one of two broken outcomes depending on how the admin configures the allowlist.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ..., extensionData)   // msg.sender = router
     → _beforeSwap(msg.sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router address when the user entered through the router: [3](#0-2) 

The router never injects the original user's address into the pool call; it simply calls `pool.swap(...)` with itself as `msg.sender`: [4](#0-3) 

Because the pool's `swap()` function issues a `metricOmmSwapCallback` to `msg.sender` for token settlement, ordinary EOAs **cannot** call `pool.swap()` directly — they must go through the router. The router is therefore the only practical entry point for most users.

---

### Impact Explanation

Two mutually exclusive broken states arise:

**State A — Router is NOT allowlisted (most likely admin intent):**
The pool admin allowlists specific user addresses. Every user who calls the router hits `allowedSwapper[pool][router] == false` and receives `NotAllowedToSwap`. Allowlisted users are completely locked out of swapping because they cannot bypass the router.

**State B — Router IS allowlisted (admin workaround to unblock users):**
`allowedSwapper[pool][router] == true` passes for every call through the router regardless of who the original user is. Any non-allowlisted address can swap by routing through `MetricOmmSimpleRouter`, defeating the entire allowlist. The curated pool's access control is silently nullified.

Both states represent a broken core invariant: the allowlist either blocks all router-mediated swaps or allows all of them. There is no configuration that correctly gates individual users through the router.

---

### Likelihood Explanation

The trigger is a standard `exactInputSingle` or `exactInput` call on any pool that has `SwapAllowlistExtension` registered in its `BEFORE_SWAP_ORDER`. No special permissions, flash loans, or unusual token behavior are required. Any user who calls the router on such a pool reaches the broken check. The router is the documented and expected periphery entry point. [5](#0-4) 

---

### Recommendation

The pool's `swap()` interface does not carry an explicit "original caller" field. Two remediation paths exist:

1. **Extension-side fix:** Add a `trustedForwarder` mapping in `SwapAllowlistExtension` that maps known router addresses to the actual user. The router would need to encode the original `msg.sender` in `extensionData`, and the extension would decode and check it. This requires a coordinated change between the router and the extension.

2. **Pool-side fix (preferred):** Add an explicit `originator` parameter to `pool.swap()` (or pass it through `extensionData` at the router level) so the pool can forward the true caller identity to extensions. The router would pass `msg.sender` as the originator, and the extension would check that value instead of the `sender` argument.

Until fixed, pools relying on `SwapAllowlistExtension` for access control should not be deployed with the public router as a supported entry point.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension in BEFORE_SWAP_ORDER.
// 2. Pool admin allowlists `allowedUser` but NOT the router.
// 3. Pool admin does NOT allowlist the router.

// Demonstrate State A: allowlisted user blocked through router
function test_allowlisted_user_blocked_via_router() public {
    // allowedUser is on the allowlist
    swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
    assertFalse(swapExtension.isAllowedToSwap(address(pool), address(router)));

    // allowedUser tries to swap through the router
    vm.prank(allowedUser);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        recipient: allowedUser,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp + 1,
        extensionData: ""
    }));
    // Reverts even though allowedUser is on the allowlist.
    // The extension saw sender=router, not sender=allowedUser.
}

// Demonstrate State B: non-allowlisted user bypasses via router when router is allowlisted
function test_non_allowlisted_user_bypasses_via_router() public {
    // Admin allowlists the router (workaround for State A)
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    assertFalse(swapExtension.isAllowedToSwap(address(pool), blockedUser));

    // blockedUser (NOT on allowlist) swaps through the router — succeeds
    vm.prank(blockedUser);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        recipient: blockedUser,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp + 1,
        extensionData: ""
    }));
    // Succeeds. Allowlist completely bypassed.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```
