### Title
`SwapAllowlistExtension` checks router address as swapper identity, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the `sender` the extension receives is the **router's address**, not the actual user's address. The allowlist is keyed by individual user addresses, so the identity the extension checks is structurally different from the identity the pool admin intended to gate — an exact analog to the H-03 hash-mismatch class.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `sender` (the first parameter) against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router contract**, so the extension receives `sender = address(MetricOmmSimpleRouter)`. The allowlist entry the pool admin created for an individual user (e.g. `allowedSwapper[pool][userA] = true`) is never consulted; the extension looks up `allowedSwapper[pool][router]` instead.

This creates an irresolvable dilemma for any pool admin who deploys `SwapAllowlistExtension`:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert — even for users on the allowlist |
| Router **allowlisted** | Every address in existence can swap through the router, defeating the allowlist entirely |

The same structural mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in `MetricOmmSimpleRouter`. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g. institutional market makers, whitelisted LPs, or KYC'd addresses) can be fully bypassed by any unprivileged user calling `MetricOmmSimpleRouter`. The unauthorized user executes swaps at live oracle prices against LP capital that was deposited under the assumption that only vetted counterparties would trade. This constitutes direct loss of LP principal through unauthorized extraction at oracle-fair prices on a pool whose access policy was supposed to prevent it.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface documented in the protocol. Any pool that (a) deploys `SwapAllowlistExtension` and (b) needs to support router-mediated swaps for its allowed users is immediately vulnerable. The attacker needs no special role, no tokens beyond the swap input, and no privileged setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The router must forward the originating user's address so the extension can gate on the correct identity. Two viable approaches:

1. **Encode the real sender in `extensionData`**: The router appends `msg.sender` to `extensionData` before forwarding it to the pool. `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of (or in addition to) `sender`.

2. **Add a `realSender` field to the swap interface**: The pool passes an explicit `realSender` to extensions, populated by the router from its own `msg.sender` and by direct callers from their own `msg.sender`.

Either fix must be authenticated (the extension must verify the router is the declared intermediary) to prevent spoofing.

---

### Proof of Concept

```
Setup:
  pool P has SwapAllowlistExtension E configured
  admin sets allowedSwapper[P][alice] = true   // alice is the only allowed swapper
  admin sets allowedSwapper[P][router] = true  // necessary so alice can use the router

Attack:
  bob (not on allowlist) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})

  Execution path:
    router.exactInputSingle()
      -> pool.swap(recipient, ...) [msg.sender = router]
        -> _beforeSwap(sender=router, ...)
          -> SwapAllowlistExtension.beforeSwap(sender=router, ...)
            -> allowedSwapper[pool][router] == true  ✓  (passes)
        -> swap executes against LP funds

Result: bob swaps successfully on a pool that was supposed to block him.
```

If the admin does NOT allowlist the router to prevent this, then alice also cannot use the router — the allowlist becomes router-incompatible, breaking core swap functionality for legitimate users.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
