### Title
`SwapAllowlistExtension` gates the router address instead of the real user, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the address that called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that caller is the router contract, not the end user. A pool admin who adds the router to the allowlist (to enable router-mediated swaps for legitimate users) simultaneously opens the gate to every address on the network.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument of the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — in every case the pool sees the router as `msg.sender`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][real_user]`. [5](#0-4) 

---

### Impact Explanation

A pool admin deploying a curated pool with `SwapAllowlistExtension` faces an inescapable dilemma:

- **Router not on allowlist**: every allowlisted user is silently blocked from using the supported periphery path (`MetricOmmSimpleRouter`), breaking core swap functionality for legitimate users.
- **Router added to allowlist** (the only way to re-enable router swaps): the check degenerates to `allowedSwapper[pool][router]` = `true` for every call, so any address on the network can swap on the curated pool by routing through the router. The per-user allowlist is completely bypassed.

The second scenario is a direct loss-of-curation impact: a pool designed to restrict trading to a specific set of counterparties is open to the public.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported swap interface for EOAs. Any pool that uses `SwapAllowlistExtension` and also wants to support router users will add the router to the allowlist, triggering the bypass. The trigger requires no special privilege — any public user can call `exactInputSingle` on the router pointing at the curated pool.

---

### Recommendation

The extension must receive the original end-user identity, not the intermediary. Two complementary fixes:

1. **Pass the original sender through the router**: `MetricOmmSimpleRouter` should forward `msg.sender` (the real user) as a dedicated `originator` field in `extensionData`, and `SwapAllowlistExtension` should decode and check that field when present.

2. **Alternatively, check `recipient` instead of `sender`**: for swap allowlists the economically relevant actor is often the recipient of output tokens. The extension could be parameterized to check `recipient` (second argument of `beforeSwap`) rather than `sender`.

The cleanest long-term fix is for the pool to pass both the direct caller and an optional `originator` so extensions can choose which identity to gate.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  - Pool admin calls setAllowedToSwap(pool, router, true)  // required so alice can use the router

Attack:
  - bob (not on allowlist) calls MetricOmmSimpleRouter.exactInputSingle(
        pool    = curated_pool,
        sender  = bob,          // bob is msg.sender of the router
        ...
    )
  - Router calls pool.swap(recipient, ...) with msg.sender = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Bob's swap executes on the curated pool despite not being on the allowlist.
```

The allowlist is fully bypassed. Any user who routes through `MetricOmmSimpleRouter` is indistinguishable from the router itself as far as `SwapAllowlistExtension` is concerned.

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
