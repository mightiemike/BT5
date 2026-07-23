### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Economic Actor, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the user. If the pool admin allowlists the router (the natural action to enable router-mediated swaps on a curated pool), every unprivileged user can bypass the per-user allowlist by calling any of the router's public swap entry points.

---

### Finding Description

**Actor binding in the pool:**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**The guard checks the wrong actor:**

`SwapAllowlistExtension.beforeSwap` gates on `sender` (the immediate caller of `pool.swap()`), keyed by `msg.sender` (the pool): [3](#0-2) 

**The router is the immediate caller:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly. The pool sees `msg.sender = router`: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Result:** The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. If the pool admin allowlists the router address (the natural action to let users trade through the standard periphery), the check passes for every caller regardless of their individual allowlist status.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool — the admin intends to restrict trading to specific counterparties (e.g., KYC'd users, institutional market makers, or whitelisted addresses). Allowlisting the router is the only way to let those users access the pool through the standard periphery interface. Once the router is allowlisted, the per-user allowlist is completely inoperative: any address can call `exactInputSingle` or any other router entry point and trade on the pool. LPs who deposited under the assumption that only vetted counterparties would trade against them are exposed to unrestricted arbitrage and value extraction at oracle prices, constituting a direct loss of LP principal above contest thresholds.

---

### Likelihood Explanation

High. The router is the primary user-facing interface for the protocol. A pool admin who wants to run a curated pool but still allow users to use the standard router will inevitably allowlist the router address. The admin has no on-chain signal that doing so opens the allowlist to everyone. The bypass requires no special privilege — any EOA calling `MetricOmmSimpleRouter.exactInputSingle` with the target pool triggers it.

---

### Recommendation

The extension must gate on the **economic actor** (the originating user), not the immediate caller of `pool.swap()`. Two approaches:

1. **Encode the original user in `extensionData`** and have the router always append `msg.sender` there; the extension reads and verifies it. This requires a coordinated change in the router and extension.

2. **Check `recipient` instead of `sender`** if the pool's design guarantees that the recipient is always the economic actor. This is fragile for multi-hop paths.

3. **Preferred:** Add a dedicated `swapper` field to the `swap()` interface (separate from `recipient`) that the router always sets to `msg.sender`, and have the extension gate on that field. Until then, document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and that allowlisting the router negates the guard entirely.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it
  - bob is NOT in allowedSwapper[pool]

Direct swap by bob (blocked as expected):
  vm.prank(bob);
  pool.swap(bob, true, 1000, type(uint128).max, "", "");
  // → reverts NotAllowedToSwap ✓

Router swap by bob (bypass):
  vm.prank(bob);
  router.exactInputSingle(ExactInputSingleParams({
      pool:           address(pool),
      recipient:      bob,
      zeroForOne:     true,
      amountIn:       1000,
      amountOutMinimum: 0,
      priceLimitX64:  0,
      deadline:       block.timestamp,
      tokenIn:        token0,
      extensionData:  ""
  }));
  // → succeeds; extension saw sender=router which is allowlisted
  // bob traded on a pool he was explicitly excluded from
```

The check `allowedSwapper[pool][router] == true` passes for every user who routes through the router, making the per-user allowlist a no-op.

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
