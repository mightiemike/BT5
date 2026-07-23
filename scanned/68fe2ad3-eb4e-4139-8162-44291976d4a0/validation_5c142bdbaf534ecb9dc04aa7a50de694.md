### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` — the address the pool passes as the first argument — against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted rather than whether the **actual user** is allowlisted. If a pool admin allowlists the router to enable router-based swaps, every unprivileged user can bypass the allowlist by routing through it.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router**, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]` — the router's allowlist entry — rather than the actual user's entry. The originating user's identity is stored only in the router's transient callback context (`_getPayer()`) and is never surfaced to the extension.

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router address (a natural step to let their approved users trade via the standard periphery) inadvertently opens the pool to **every** user. Any address can call `router.exactInputSingle` and the extension will pass because `allowedSwapper[pool][router] = true`. The allowlist is completely bypassed, allowing unauthorized traders to execute swaps on a pool that was intended to be restricted. This constitutes a direct policy bypass on a curated pool and can result in unauthorized price impact, fee extraction, or interaction with pools that carry regulatory or risk-management restrictions.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any pool admin who wants their allowlisted users to be able to use the router must allowlist the router address — there is no other mechanism. The bypass is therefore triggered by a routine, expected admin action. An attacker needs only to call the public router with any swap parameters; no special privileges, flash loans, or multi-step setup are required.

---

### Recommendation

The extension must check the **originating user**, not the immediate caller of `pool.swap`. Two sound approaches:

1. **Pass the originating user through the pool.** Add an `originator` field to the swap call or extension data so the pool can forward the true user identity. The router would populate this field with `msg.sender` before calling the pool.

2. **Check `sender` in the extension but require the router to be non-allowlistable.** Document that the router must never be added to any pool's allowlist and enforce this at the factory level by rejecting router-address entries.

The cleanest fix is approach 1: the pool's `swap` signature already accepts `extensionData`; the router can encode the originating user there, and the extension can decode and check it. This preserves the allowlist semantics regardless of which periphery contract initiates the swap.

---

### Proof of Concept

```
Setup:
  pool deployed with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps for approved users
  admin calls setAllowedToSwap(pool, alice, true)    // alice is an approved user
  bob is NOT in the allowlist

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
    → pool: msg.sender = router
    → pool calls _beforeSwap(router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes for bob despite bob not being allowlisted

Result:
  bob successfully trades on a curated pool he was never authorized to access.
  The allowlist guard is silently bypassed for every router-mediated swap.
``` [6](#0-5) [7](#0-6)

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
