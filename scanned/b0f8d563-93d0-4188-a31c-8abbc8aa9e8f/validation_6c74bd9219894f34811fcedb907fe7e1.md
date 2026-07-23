### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router (the natural configuration for a pool that supports router-mediated swaps), every user on the network can bypass the swap allowlist.

---

### Finding Description

**Pool → Extension argument binding**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

**What the allowlist extension checks**

`SwapAllowlistExtension.beforeSwap` gates on `sender` (the first parameter) keyed by `msg.sender` (the pool): [3](#0-2) 

**What the router passes**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The pool's `msg.sender` is therefore the router contract, not the end user: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**The mismatch**

| Entry path | `sender` seen by extension | Allowlist lookup |
|---|---|---|
| User calls `pool.swap()` directly | `user` | `allowedSwapper[pool][user]` ✓ |
| User calls `router.exactInputSingle()` | `router` | `allowedSwapper[pool][router]` ✗ |

A pool admin who wants to support router-mediated swaps will allowlist the router address. Once `allowedSwapper[pool][router] = true`, the check `!allowedSwapper[msg.sender][sender]` evaluates to `false` for every user who routes through the router, so the gate is permanently open to the entire public.

---

### Impact Explanation

**Direct allowlist bypass on curated pools.** The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may trade on a curated pool. When the router is allowlisted (the only way to let legitimate users use the supported periphery path), the guard fails open for all callers. Unauthorized users can execute swaps, draining LP assets at oracle-quoted prices and collecting output tokens they were never meant to receive. This is a direct loss of LP principal and a complete failure of the pool's curation policy.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the primary public swap entrypoint documented in the protocol. Any pool that wants to support normal user swaps through the router must allowlist it. Once that configuration is in place, the bypass is trivially reachable by any address with no special privileges, no prior state manipulation, and no capital requirement beyond the swap input.

---

### Recommendation

The extension must resolve the actual end-user identity rather than the immediate caller. Two sound approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before calling the pool. The extension decodes and checks that address. This requires a coordinated convention between the router and the extension.

2. **Check `sender` only for direct pool calls; decode user from `extensionData` for router calls**: The extension inspects whether `sender` is a known router and, if so, decodes the real user from `extensionData`.

Either approach must be enforced consistently across `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (natural config: "let users swap through the router").
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true).

Attack:
  1. Alice (not allowlisted) calls router.exactInputSingle({pool: pool, ...}).
  2. Router calls pool.swap(...); pool's msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. Extension checks allowedSwapper[pool][router] → true → passes.
  5. Alice's swap executes at oracle price; she receives output tokens.

Expected: revert NotAllowedToSwap (alice is not on the allowlist).
Actual:   swap succeeds; allowlist is bypassed.
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
