### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. The pool passes `msg.sender` of its own `swap` call as that argument. When a user routes through the public `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. The extension therefore checks whether the router is allowlisted, not whether the real swapper is allowlisted. Any pool admin who allowlists the router to support router-mediated swaps simultaneously opens the gate to every user on earth, defeating the entire curation policy.

---

### Finding Description

**Call chain (direct swap — guard works correctly):**

```
user → pool.swap(...)
         msg.sender = user
         _beforeSwap(sender=user, ...)
         SwapAllowlistExtension.beforeSwap(sender=user)
         checks allowedSwapper[pool][user]  ✓
```

**Call chain (router-mediated swap — guard is bypassed):**

```
user → MetricOmmSimpleRouter.exactInputSingle(params)
         pool.swap(params.recipient, ...)   // router is msg.sender
           msg.sender = router
           _beforeSwap(sender=router, ...)
           SwapAllowlistExtension.beforeSwap(sender=router)
           checks allowedSwapper[pool][router]  ← wrong actor
```

In `MetricOmmPool.swap`, `msg.sender` is forwarded verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` value and passes it to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` the pool sees: [4](#0-3) 

The same substitution occurs in `exactInput` (intermediate hops use `address(this)` = router) and in `_exactOutputIterateCallback` (recursive hops use the previous pool as `msg.sender`): [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a **curated pool** — the admin intends to restrict trading to specific addresses (e.g., KYC-verified counterparties, institutional desks, or whitelisted market makers). The allowlist is the only on-chain enforcement of that policy.

Once the pool admin allowlists the router address (a necessary step to let allowlisted users trade via the standard periphery), the guard becomes vacuous: **any address** can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will see `sender = router`, which is allowlisted, and pass. The disallowed user executes a full swap against the pool's liquidity at oracle-derived prices, extracting output tokens they were never supposed to receive. LP funds are directly at risk because the pool's bid/ask spread and liquidity depth were calibrated for a controlled counterparty set.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless periphery contract — no special role or token is required to call it.
- The bypass requires zero privileged access: any EOA or contract can call `exactInputSingle` with the target pool address.
- The precondition (router is allowlisted) is the natural operational state for any pool that wants to support standard UX; without it, even legitimate allowlisted users cannot use the router.
- No admin action is needed to trigger the bypass; it is always active once the router is allowlisted.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` must gate the **economically relevant actor** — the end user — not the immediate `msg.sender` of the pool's `swap` call. Two sound approaches:

1. **Check `recipient` instead of `sender`** if the pool's design guarantees that the recipient is always the beneficiary. This is fragile because `recipient` can also be set to a third party.

2. **Require direct pool calls only** — document that pools using `SwapAllowlistExtension` must not allowlist any router or intermediary, and that all allowlisted users must call `pool.swap` directly. This breaks standard UX.

3. **Preferred fix — pass the original user through transient storage**: mirror the pattern used by `MetricOmmPoolLiquidityAdder` (which stores the payer in transient storage and reads it in the callback). The router should store `msg.sender` in transient storage before calling the pool, and the extension should read that slot to identify the real swapper. This requires a coordinated change to the router and the extension interface.

---

### Proof of Concept

```solidity
// Setup:
// - pool has SwapAllowlistExtension configured as beforeSwap hook
// - pool admin allowlists: userA (legitimate), router (for UX)
// - userB is NOT allowlisted

// userB bypasses the allowlist:
MetricOmmSimpleRouter.ExactInputSingleParams memory params = MetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(curated_pool),
    tokenIn: token0,
    recipient: userB,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp + 1,
    extensionData: ""
});

// userB calls the public router — no allowlist check on userB
vm.prank(userB);
router.exactInputSingle(params);
// pool.swap is called with msg.sender = router
// SwapAllowlistExtension checks allowedSwapper[pool][router] → true
// swap executes; userB receives output tokens
// NotAllowedToSwap is never reverted
```

The `SwapAllowlistExtension` unit tests only exercise the direct-pool path (`vm.prank(address(pool)); extension.beforeSwap(swapper, ...)`) and never test the router-mediated path, so the bypass is untested and undetected: [7](#0-6) 

The integration test in `FullMetricExtensionTest` also calls the pool directly through a `TestCaller` wrapper, not through `MetricOmmSimpleRouter`, leaving the router bypass path uncovered: [8](#0-7)

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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-38)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }

  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-74)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }

  function test_blocksDepositWhenDepositorNotAllowed() public {
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    _addLiquidity(0, -5, 4, 10_000, EXTENSION_TEST_SALT);
  }

  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
