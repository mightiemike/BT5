### Title
SwapAllowlistExtension gates the router address instead of the real user, letting any caller bypass the per-user swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the **router contract**, not the end user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for any allowlisted user), every unpermissioned address can bypass the allowlist by routing through the router.

---

### Finding Description

**Call path:**

```
user → MetricOmmSimpleRouter.exactInputSingle()
     → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
     → MetricOmmPool._beforeSwap(msg.sender=router, ...)
     → ExtensionCalling._callExtensionsInOrder(BEFORE_SWAP_ORDER, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

**Pool passes `msg.sender` as `sender` to every before-swap hook:** [1](#0-0) 

**`ExtensionCalling._beforeSwap` forwards that value unchanged:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` (the router) is allowlisted:** [3](#0-2) 

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making itself `msg.sender` inside the pool:** [4](#0-3) 

The same pattern holds for `exactOutputSingle`, `exactInput`, and `exactOutput` — in every case the router is `msg.sender` of the pool call. [5](#0-4) 

**The invariant that breaks:** the extension is supposed to gate the *economic actor* (the user who initiates and pays for the swap). Instead it gates the *intermediary* (the router). These are different addresses whenever the router is used.

---

### Impact Explanation

Two fund-impacting outcomes follow directly:

1. **Allowlist bypass (High):** A pool admin who allowlists the router — the natural configuration to let any allowlisted user trade through the router — inadvertently opens the pool to *every* address. Any unpermissioned user calls `exactInputSingle` or `exactInput` through the router and the extension passes because `allowedSwapper[pool][router] == true`. The curated pool's access control is completely defeated; disallowed users can drain liquidity at oracle prices.

2. **Broken core functionality for allowlisted users (Medium):** If the pool admin does *not* allowlist the router (to avoid the bypass above), then every allowlisted user is silently locked out of the router. They can only call `pool.swap()` directly, losing slippage protection, multi-hop routing, and deadline enforcement. This is a broken core pool flow for the intended user set.

---

### Likelihood Explanation

The trigger requires no privileged action beyond the pool admin allowlisting the router — a configuration that is both documented as the intended periphery entry point and expected by users. The pool admin is semi-trusted and would not recognize this as a security-relevant step. Any public user can then exploit it by calling the router with standard parameters. No special tokens, flash loans, or oracle manipulation are needed.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` must check the *originating user*, not the intermediary. The pool already passes both `sender` (the direct caller) and `recipient` to the hook. The correct fix is to expose the real user through a separate mechanism — either:

- Require that direct pool callers always equal the intended swapper (i.e., disallow router-mediated swaps on allowlisted pools), or
- Pass the real user address as part of `extensionData` and have the router populate it, with the extension verifying it against `msg.sender` of the router call.

The simplest safe fix is to document and enforce that `SwapAllowlistExtension` is incompatible with router-mediated swaps, and revert in `beforeSwap` if `sender != tx.origin` (or use a dedicated forwarded-sender field in `extensionData`).

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as EXTENSION_1, BEFORE_SWAP_ORDER = 1
  pool admin calls setAllowedToSwap(pool, alice, true)      // alice is the intended user
  pool admin calls setAllowedToSwap(pool, router, true)     // to let alice use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
    })

  Execution:
    router → pool.swap(bob, true, X, ...)
      pool: sender = msg.sender = router
      _beforeSwap(sender=router, ...)
      SwapAllowlistExtension.beforeSwap(sender=router, ...)
        allowedSwapper[pool][router] == true  → passes
      swap executes, bob receives tokens

Result:
  bob, who is not in the allowlist, successfully swaps on a curated pool.
  The allowlist invariant is broken; any user can bypass it via the router.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
