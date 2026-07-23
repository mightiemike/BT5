### Title
`SwapAllowlistExtension` gates the router address instead of the end user, enabling complete allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool. The pool always sets that argument to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. The extension therefore checks whether the router is allowlisted, not whether the actual trader is allowlisted. Any non-allowlisted user can bypass a curated pool's swap gate by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every before-swap extension hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

The pool's `msg.sender` is therefore the **router address**. The extension receives `sender = router` and evaluates `allowedSwapper[pool][router]` — never touching the actual end user's allowlist entry.

The same misbinding applies to `exactInput` multi-hop paths: [5](#0-4) 

---

### Impact Explanation

Two fund-impacting outcomes arise:

1. **Allowlist bypass (High):** If the pool admin allowlists the router address (the only way to let any user reach the pool through the router), every non-allowlisted user can swap freely by routing through `MetricOmmSimpleRouter`. The curated pool's access control is completely defeated, allowing unauthorized traders to drain liquidity at oracle prices.

2. **Broken core functionality (Medium):** If the admin does not allowlist the router, allowlisted users cannot use the router at all — their own allowlist entry is never checked. The supported periphery path is silently unusable for every legitimate user.

Both outcomes are reachable by any public caller with no privileged setup.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point documented for swaps. Any pool that deploys `SwapAllowlistExtension` to restrict trading to a curated set of addresses will encounter this misbinding the first time a user (or the admin, testing the integration) routes through the router. The trigger requires no special timing, no oracle manipulation, and no admin cooperation beyond the normal pool setup.

---

### Recommendation

The extension must gate the **economic actor** — the end user — not the intermediate contract. Two complementary fixes:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address. This requires a coordinated convention between the router and the extension.

2. **Check `recipient` instead of `sender` for swap allowlists:** For exact-input swaps the recipient is the intended beneficiary; gating on `recipient` is semantically closer to the intended policy and is not spoofable by the router.

The cleanest long-term fix is option 1, with the router always prepending the original caller's address to `extensionData` so any extension can recover the true initiator.

---

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured in the beforeSwap order.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — necessary so that any router-mediated swap can reach the pool at all.
3. Pool admin calls setAllowedToSwap(pool, alice, true)  // alice is the only intended trader
   — bob is NOT allowlisted.
4. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
   — Router calls pool.swap(recipient=bob, ...).
   — Pool calls _beforeSwap(sender=router, ...).
   — Extension checks allowedSwapper[pool][router] == true → passes.
   — Bob's swap executes despite never being allowlisted.
5. Alice calls pool.swap() directly.
   — Pool calls _beforeSwap(sender=alice, ...).
   — Extension checks allowedSwapper[pool][alice] == true → passes (correct).
6. Carol (not allowlisted) calls pool.swap() directly.
   — Extension checks allowedSwapper[pool][carol] == false → reverts (correct).

Result: Bob (step 4) bypasses the allowlist entirely through the router,
        while Carol (step 6) is correctly blocked on the direct path.
        The allowlist provides zero protection for router-mediated swaps.
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
