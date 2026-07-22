### Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender` instead of the actual end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` - (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual end user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. If the router address is allowlisted (a natural admin action for a pool that uses the router as its standard entry point), any unprivileged user can bypass the per-user swap allowlist entirely.

---

### Finding Description

The call chain is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...) [msg.sender = router]
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → checks allowedSwapper[pool][router]  ← WRONG ACTOR
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to the extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router address when the user routes through the periphery: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the explicit position owner passed by the caller), not `sender`, so the deposit guard is not affected: [6](#0-5) 

---

### Impact Explanation

**Allowlist bypass (High):** A pool admin deploys `SwapAllowlistExtension` to restrict swaps to a curated set of addresses. To allow those users to trade through the standard periphery, the admin allowlists the router address (`setAllowedToSwap(pool, router, true)`). Because the extension checks the router address — not the individual user — every address in existence can now call `MetricOmmSimpleRouter` and pass the guard. The per-user allowlist is completely nullified for all router-based swaps.

**Broken core functionality (Medium):** If the admin allowlists individual users but not the router, those allowlisted users cannot trade through `MetricOmmSimpleRouter` at all. Their swaps revert with `NotAllowedToSwap` because the extension sees the router address, which is not on the list. The only workaround is to call the pool directly, bypassing the standard periphery entirely.

Both consequences are fund-impacting: the first allows unauthorized value extraction from a curated pool; the second prevents legitimate LPs and traders from using the supported withdrawal/swap flow.

---

### Likelihood Explanation

The trigger is a normal, valid admin action: allowlisting the router so that curated-pool users can trade through the standard periphery. This is the expected operational pattern for any pool that uses `MetricOmmSimpleRouter` as its entry point. No malicious setup, no non-standard tokens, and no privileged attacker role is required — any unprivileged user calling the public router function is sufficient.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the **economic actor** — the address that initiated the trade and will pay the input tokens — not on the immediate caller of the pool. Two options:

1. **Pass the originating user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a protocol-level convention.

2. **Check `recipient` instead of `sender` (partial fix):** For single-hop swaps the recipient is often the user, but this breaks for multi-hop routes where intermediate recipients are the router itself.

3. **Preferred — mirror the deposit pattern:** Introduce an explicit `swapper` parameter to `pool.swap()` (analogous to `owner` in `addLiquidity`) that the caller sets to the true economic actor. The pool enforces `msg.sender == swapper || isApprovedRouter(msg.sender)` and passes `swapper` to the extension. The extension then checks `allowedSwapper[pool][swapper]`.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` attached to `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, address(router), true)` — the natural action to enable router-based trading.
3. A non-allowlisted user (`alice`) calls `router.exactInputSingle(...)` targeting the pool.
4. The pool calls `_beforeSwap(msg.sender=router, ...)`.
5. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
6. Alice's swap executes despite never being individually allowlisted.

Conversely, if the admin allowlists `alice` directly but not the router:

3. Alice calls `router.exactInputSingle(...)`.
4. Extension evaluates `allowedSwapper[pool][router] == false` → reverts `NotAllowedToSwap`.
5. Alice cannot use the supported periphery path even though she is individually authorized.

The `FullMetricExtensionTest` integration test confirms the extension is wired to the pool and that the `sender` field drives the allowlist decision: [7](#0-6) 

In that test, `callers[0]` (a `TestCaller` that calls the pool **directly**) is allowlisted — not the router — confirming that the test suite never exercises the router path against the allowlist, leaving the bypass untested.

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
