### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass Per-User Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the user. If the pool admin allowlists the router (the natural action to enable router-mediated swaps for their allowlisted users), every unprivileged user can bypass the per-user allowlist by routing through the public router.

### Finding Description

**Call chain for a direct swap (works as intended):**

```
alice → pool.swap(...)
         msg.sender = alice
         _beforeSwap(sender=alice, ...)
         extension.beforeSwap(sender=alice, ...)
         allowedSwapper[pool][alice] → true ✓
```

**Call chain for a router-mediated swap (bypass):**

```
bob → MetricOmmSimpleRouter.exactInputSingle({pool: curatedPool, ...})
        router calls pool.swap(recipient, ...)
        msg.sender at pool = router
        _beforeSwap(sender=router, ...)
        extension.beforeSwap(sender=router, ...)
        allowedSwapper[pool][router] → true ✓  (bob bypasses check)
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly, making itself the `msg.sender` the pool sees: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict trading to specific addresses (e.g., KYC-verified users, institutional counterparties). To also support router-mediated swaps for those allowlisted users, the admin must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`, and any unprivileged user can call `MetricOmmSimpleRouter` to swap on the curated pool. The per-user allowlist is completely nullified. Unauthorized users gain full swap access to a pool whose design intent is to restrict trading to vetted participants.

**Impact: Medium** — direct policy bypass on curated pools; unauthorized users trade on restricted pools.

### Likelihood Explanation

The bypass requires two conditions: (1) the pool admin allowlists the router, and (2) a non-allowlisted user uses the router. Condition (1) is the natural and expected action for any admin who wants their allowlisted users to benefit from the router's slippage protection and multi-hop routing. Condition (2) is trivially achievable by any user. The existing test suite confirms the allowlist is checked against the direct caller (`callers[0]`), not the end user, and no test exercises the router path against an allowlisted pool: [6](#0-5) 

**Likelihood: Medium** — requires a plausible and expected admin configuration.

### Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor — the end user — not the intermediary router. Two viable fixes:

1. **Pass original user through `extensionData`:** Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. This requires a trust assumption that the router is the only allowed intermediary.

2. **Check `recipient` instead of `sender`:** If the pool's design guarantees that the recipient is always the actual user (not an intermediate contract), the extension can check `allowedSwapper[pool][recipient]`. However, this breaks multi-hop flows where intermediate recipients are the router itself.

3. **Document that the router must never be allowlisted** and that allowlisted users must call the pool directly. This is the least invasive fix but degrades UX for curated pools.

The cleanest architectural fix is option 1: the router should forward the original `msg.sender` in `extensionData`, and the extension should decode and verify it when the caller is a known router.

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Pool admin allowlists alice (KYC'd user) and the router (for router support)
swapExtension.setAllowedToSwap(address(pool), alice, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// alice adds liquidity (she is allowlisted for deposits too)
vm.prank(alice);
pool.addLiquidity(alice, 0, deltas, "", "");

// bob is NOT allowlisted
assertFalse(swapExtension.isAllowedToSwap(address(pool), bob));

// bob bypasses the allowlist by routing through the public router
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: bob,
        tokenIn: token1,
        zeroForOne: false,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// ✓ swap succeeds — bob traded on a pool he should not have access to
```

The router calls `pool.swap()` with `msg.sender = router`. The extension checks `allowedSwapper[pool][router]` which is `true`, so bob's swap is accepted despite bob not being allowlisted.

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
