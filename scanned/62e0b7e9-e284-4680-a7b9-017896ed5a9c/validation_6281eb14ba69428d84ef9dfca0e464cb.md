### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the value passed by the pool — which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. Any pool admin who allowlists the router to support router-mediated swaps for legitimate users simultaneously opens the pool to every user on-chain, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is whatever the pool forwarded — the router address when the call originates from `MetricOmmSimpleRouter`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` stores the real user in transient callback context (for payment) but calls `pool.swap()` directly, making itself `msg.sender` to the pool: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The result is a forced dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

- **Do not allowlist the router** → legitimate allowlisted users cannot use the router at all (broken core swap functionality).
- **Allowlist the router** → the check becomes `allowedSwapper[pool][router]`, which passes for every caller of the public router, making the allowlist meaningless.

There is no mechanism in the current design to thread the original EOA address through the router into the extension's `sender` parameter.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified market makers, whitelisted institutions, or protocol-controlled addresses) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. Once the router is allowlisted — a necessary step for the pool to be usable via the standard periphery — the allowlist provides zero protection. Unauthorized users can execute swaps against the pool's oracle-priced bins, extracting value at the oracle mid-price and disrupting the pool's intended participant set. This constitutes a broken core pool functionality and an admin-boundary break where the pool admin's configured access policy is bypassed by an unprivileged path.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin who deploys a curated pool and wants legitimate users to interact via the standard periphery must allowlist the router. The bypass requires no special privileges, no flash loans, and no complex setup — any EOA can call `exactInputSingle` on the router pointing at the allowlisted pool. The scenario is directly reachable in production.

---

### Recommendation

The `sender` forwarded to extensions must represent the economically relevant actor — the original EOA — not the intermediary router. Two approaches:

1. **Router passes original user via `extensionData`**: The router encodes `msg.sender` into the `extensionData` it forwards, and `SwapAllowlistExtension` decodes and checks it. This requires a convention between router and extension.

2. **Pool exposes a `swapFrom(address realSender, ...)` entry point**: The router calls a dedicated function that accepts the original user address, and the pool uses that address as `sender` for extension dispatch. The pool must validate the caller is a trusted router (e.g., via factory registry).

Either way, the extension must check the address of the entity whose economic action is being gated, not the address of the contract that relayed the call.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended swapper
  - Pool admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router

Attack:
  - charlie (not allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({
          pool: curated_pool,
          recipient: charlie,
          zeroForOne: true,
          amountIn: X,
          ...
      })

Trace:
  router.exactInputSingle()
    → pool.swap(recipient=charlie, ...) [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (passes!)
      → swap executes, charlie receives tokens

Result:
  charlie bypasses the allowlist because the extension checks
  allowedSwapper[pool][router] instead of allowedSwapper[pool][charlie].
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
