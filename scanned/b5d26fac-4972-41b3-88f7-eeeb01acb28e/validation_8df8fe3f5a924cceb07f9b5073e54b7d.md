### Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` Intermediary — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When `MetricOmmSimpleRouter` mediates the swap, the pool's `msg.sender` is the **router address**, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every user — including non-allowlisted ones — can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` on the user's behalf: [4](#0-3) 

From the pool's perspective `msg.sender` is the **router**, so the extension receives `sender = router_address`. The extension then evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

A pool admin who wants allowlisted users to be able to trade via the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for **every** caller of the router, regardless of whether the actual end user is on the allowlist. The per-user curation is completely defeated.

The same structural mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all of them call `pool.swap()` with `msg.sender = router`. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC-verified addresses, institutional participants) loses that restriction entirely once the router is allowlisted. Any non-allowlisted user can execute swaps against the pool's LP positions, extracting value at oracle-quoted prices that the LP providers expected only allowlisted counterparties to access. This is a direct loss of LP principal and a complete failure of the pool's curation policy.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap interface documented and deployed for end users. Any pool admin who wants allowlisted users to be able to use the router (the normal UX path) must allowlist the router address. The bypass is then reachable by any address that can call the router — no special privileges, no malicious setup, no non-standard tokens required.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **end user**, not the direct pool caller. Two complementary fixes:

1. **Pass the original initiator through the router.** The router already stores the original `msg.sender` in transient storage as the payer. The pool's `swap` interface could accept an optional `originator` hint, or the extension could read it from a trusted router registry.

2. **Alternatively, reject router-mediated swaps on allowlisted pools.** Add a check in `SwapAllowlistExtension.beforeSwap` that reverts if `sender` is a known router and the pool is not in `allowAllSwappers` mode, forcing users to call the pool directly.

The minimal safe fix is to document that allowlisting the router address opens the pool to all users, and provide a separate `RouterSwapAllowlistExtension` that reads the payer from a trusted transient-storage source rather than the raw `sender` argument.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only intended swapper
  allowedSwapper[pool][router] = true  // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: weth, ...})

  router calls pool.swap() → msg.sender = router
  _beforeSwap(sender=router, ...)
  SwapAllowlistExtension.beforeSwap:
    allowedSwapper[pool][router] == true  → passes
  bob's swap executes against LP liquidity
  alice's intended exclusivity is gone
```

Direct pool call by bob would correctly revert:
```
  pool.swap(...) → msg.sender = bob
  allowedSwapper[pool][bob] == false → NotAllowedToSwap ✓
```

The bypass is reachable by any user through the public router with no preconditions beyond the admin having allowlisted the router to support normal UX.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
