### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User When Swaps Are Routed Through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router address (to enable router-mediated swaps) inadvertently opens the pool to every user, completely bypassing the per-user allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router itself calls `pool.swap()`: [4](#0-3) 

So the pool's `msg.sender` — and therefore the `sender` the extension sees — is the **router address**, not the actual end user. The allowlist lookup becomes `allowedSwapper[pool][router]`.

A pool admin who wants to allow router-mediated swaps for their allowlisted users must add the router to the allowlist. Once the router is allowlisted, **any** user can call `router.exactInputSingle` and the extension passes, because the identity check resolves to the router, not the caller. The per-user allowlist is completely bypassed.

The same misbinding applies to `exactInput` and `exactOutput` multi-hop paths. [5](#0-4) 

---

### Impact Explanation

A curated pool that restricts swaps to a specific set of counterparties (e.g., KYC'd addresses, protocol-owned bots, or whitelisted market makers) loses that restriction entirely for any user who routes through the public `MetricOmmSimpleRouter`. Unauthorized users can drain LP value at oracle-quoted prices, extract spread fees, or trade in pools that were designed to be closed to the public. This is a direct loss of LP principal and a broken core pool invariant (access control).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap entrypoint. Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and also wants to support router-mediated swaps for their allowlisted users will naturally add the router to the allowlist. The bypass is then immediately reachable by any unprivileged user with no special setup. The trigger is a single standard `exactInputSingle` call.

---

### Recommendation

The `sender` forwarded to extensions should represent the **economic actor** (the end user), not the intermediary contract. Two complementary fixes:

1. **In the router**: pass the original `msg.sender` (the end user) as `callbackData` or a dedicated field so extensions can recover it. The pool would need to forward this through `extensionData`.
2. **In `SwapAllowlistExtension`**: document clearly that `sender` is the direct pool caller, and provide a separate mechanism (e.g., a signed permit or an `extensionData`-encoded user address verified against a signature) for router-mediated identity checks.

The simplest safe fix is to have the router encode the real user address into `extensionData` and have the extension decode and verify it (with a signature or trusted-forwarder pattern), rather than relying on the raw `sender` argument.

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to allow router-mediated swaps for their allowlisted users.
3. Attacker (not in the per-user allowlist) calls:
     router.exactInputSingle({pool: pool, ..., recipient: attacker})
4. Pool calls _beforeSwap(msg.sender=router, ...)
5. Extension checks allowedSwapper[pool][router] == true → passes.
6. Attacker receives output tokens; the per-user allowlist was never consulted.
```

The `FullMetricExtensionTest` test suite confirms the extension is wired through the pool's `beforeSwap` hook and that `sender` is the direct pool caller: [6](#0-5) 

All existing tests exercise only direct pool calls (`callers[i]` → `pool.swap`), never the router path, so the misbinding is untested and the bypass is live on any router-mediated flow.

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
