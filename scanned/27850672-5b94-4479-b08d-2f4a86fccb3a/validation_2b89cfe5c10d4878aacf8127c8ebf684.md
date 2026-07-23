### Title
SwapAllowlistExtension Gates the Router Address Instead of the Original User, Allowing Full Allowlist Bypass via the Periphery Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the user. A pool admin who allowlists the router to support periphery-mediated swaps inadvertently opens the allowlist to every user on the network, completely defeating the curation policy.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` and dispatches it to each extension in order: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly — making the router the pool's `msg.sender`: [4](#0-3) 

The router stores the original user in transient storage for the payment callback only; it never forwards the user's identity to the pool or to any extension. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Consequence — two mutually exclusive failure modes:**

| Admin configuration | Effect |
|---|---|
| Allowlist specific user addresses only (not the router) | Router-mediated swaps by those users revert — the supported periphery path is unusable for allowlisted users |
| Allowlist the router address to fix the above | Every user on the network can bypass the allowlist by routing through `MetricOmmSimpleRouter` |

There is no configuration that simultaneously (a) permits allowlisted users to swap via the router and (b) blocks non-allowlisted users from doing the same, because the extension cannot distinguish the original user once the router is the caller.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router to support the standard periphery path loses all access control over who can trade. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and execute swaps on the curated pool. This breaks the pool's intended curation policy and allows disallowed counterparties to extract value from LP positions that were sized and priced for a restricted set of traders.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the documented, supported periphery path for end users. A pool admin who wants to deploy a curated pool while still supporting the standard router will naturally allowlist the router address — the extension's `setAllowedToSwap` API gives no indication that doing so opens the gate to all users. The misconfiguration is easy to make and the bypass requires no special privileges: any user with a standard EOA can call the router.

---

### Recommendation

The router must forward the original user's identity to the pool in a way that extensions can consume it. Two complementary fixes:

1. **Router-side**: Pass the original `msg.sender` as the first element of `extensionData` (or a dedicated field) so extensions can decode the true initiator.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode the original user from `extensionData` when `sender` is a known router, or the pool should expose a dedicated "original sender" slot that the router populates via transient storage before calling `pool.swap()`.

Alternatively, document explicitly that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and that per-user curation is only enforceable through direct pool calls — but this effectively makes the extension incompatible with the supported periphery.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, router, true)   // to support periphery swaps
  admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended allowed user
  // bob is NOT allowlisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
  })

  // Router calls pool.swap(...) — msg.sender to pool = router
  // Pool calls _beforeSwap(sender=router, ...)
  // SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  // Swap executes successfully for bob despite bob not being allowlisted

Result:
  bob swaps on a curated pool he was explicitly excluded from.
  The allowlist invariant is broken for every non-allowlisted user.
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
