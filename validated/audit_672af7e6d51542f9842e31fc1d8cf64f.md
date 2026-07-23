### Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of actual swapper, enabling complete allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against the per-pool allowlist. Because `sender` is `msg.sender` of `pool.swap()`, it equals the **router address** — not the originating user — whenever a swap is routed through `MetricOmmSimpleRouter`. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, the allowlist is completely bypassed for every user.

---

### Finding Description

**Step 1 — What the extension checks.**

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` is the pool (correct). `sender` is the first argument passed by the pool. [1](#0-0) 

**Step 2 — What `sender` actually is.**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, forwarding its own `msg.sender` as `sender`: [2](#0-1) 

`ExtensionCalling._beforeSwap` encodes that value as the first positional argument to every extension: [3](#0-2) 

**Step 3 — What `msg.sender` is when the router is used.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The pool's `msg.sender` is therefore the **router contract**, not the originating EOA: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap()` from the router's context. [5](#0-4) 

**Step 4 — The misbound check.**

The extension therefore evaluates `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][actual_user]`. There is no mechanism in the extension or the pool to recover the originating EOA from `extensionData` or any other channel.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (the natural configuration to let approved users trade via the standard periphery) inadvertently opens the pool to **every user**. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will pass because it sees the allowlisted router address, not the caller. The allowlist provides zero protection on the router path, which is the primary public swap entrypoint.

Conversely, if the admin does **not** allowlist the router, allowlisted users are silently blocked from using the router, breaking the core swap flow for legitimate participants.

Both outcomes are fund-impacting: the first allows unauthorized trading on a curated pool; the second renders the pool's primary swap path unusable for its intended users.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the documented, supported public swap entrypoint. A pool admin who configures a swap allowlist and wants approved users to trade via the router must allowlist the router — there is no other supported path. This is the expected operational configuration, making the bypass reachable by any unprivileged user on any allowlisted pool that also permits router access.

---

### Recommendation

The extension must gate the **originating user**, not the intermediary. Two viable approaches:

1. **Encode the real sender in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted router check (e.g., verify `sender` is a known factory-registered router before trusting the decoded address).

2. **Add an explicit `originalSender` field to the swap interface**: The pool passes both `msg.sender` (the immediate caller) and an `originalSender` (the EOA, defaulting to `msg.sender` for direct calls, set by the router for mediated calls). Extensions then check `originalSender`.

The deposit allowlist correctly avoids this problem by checking `owner` (the position owner explicitly supplied by the caller) rather than `sender` (the immediate `msg.sender`). [6](#0-5) 

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — alice is the intended gated user.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
6. Pool calls `extension.beforeSwap(router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true`.
8. Bob's swap executes successfully, bypassing the allowlist entirely.

The invariant "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it" is broken: direct calls gate by the caller's address, router calls gate by the router's address.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
