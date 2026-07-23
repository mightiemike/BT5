### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Any User to Bypass Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool sees the router as `msg.sender`, so the extension checks the router's allowlist status rather than the originating user's. A pool admin who allowlists the router to enable router-based swaps for curated users inadvertently opens the gate to every user on the network.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`), the router is the entity that calls `pool.swap()`:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
```

So `sender` arriving at the extension equals the **router address**, not the originating EOA. The extension has no way to recover the original user from this path.

**Bypass scenario:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists `alice` (`allowedSwapper[pool][alice] = true`).
2. To let `alice` use the router, the admin also allowlists the router (`allowedSwapper[pool][router] = true`).
3. `bob` (not allowlisted) calls `router.exactInputSingle(pool, ...)`. The router calls `pool.swap()`. The extension sees `sender = router`, which is allowlisted → `bob`'s swap succeeds.

The pool admin faces an impossible choice: either allowlist the router (opening the gate to everyone) or leave it out (breaking router-based swaps for all users, including allowlisted ones).

---

### Impact Explanation

Any user can trade on a curated pool that was designed to restrict access to a specific set of addresses. The allowlist protection silently fails open for all router-mediated swaps whenever the router is allowlisted. This is a direct curation failure with fund-impacting consequences: disallowed counterparties can drain liquidity from pools that were intended to be private or restricted (e.g., institutional pools, KYC-gated pools, or pools with specific market-making agreements).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Pool admins who want their allowlisted users to be able to use the router will naturally add the router to the allowlist. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a standard router call. Any user aware of the allowlist mechanism can exploit it.

---

### Recommendation

The extension must gate the **originating user**, not the intermediate router. Two approaches:

1. **Forward the original caller via `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`. The extension decodes and checks that address. This requires a trusted router convention and is fragile if other routers are added.

2. **Check `sender` only for direct pool calls; require routers to pass the original user explicitly:** Add a standardized field to `extensionData` that the extension reads when `sender` is a known router, falling back to `sender` for direct calls.

The cleanest fix is for the pool's hook interface to carry the original EOA as a separate, unforgeable field distinct from the direct caller — analogous to how the external report's fix added an OTP layer that cannot be spoofed by simply knowing the wallet address.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin: allowedSwapper[pool][alice] = true
3. Pool admin: allowedSwapper[pool][router] = true   ← required for alice to use router
4. bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, ...) with msg.sender = router.
6. Extension checks allowedSwapper[pool][router] → true → swap proceeds.
7. bob successfully swaps on a pool he was never authorized to access.
```

**Relevant code locations:** [1](#0-0) 

The extension receives `sender` (the direct pool caller) and checks it against the allowlist keyed by `msg.sender` (the pool). [2](#0-1) 

`MetricOmmPool.swap` passes `msg.sender` — the router — as `sender` to `_beforeSwap`. [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool. [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4)

### Citations

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
