### Title
SwapAllowlistExtension Checks Router Address Instead of Real Swapper, Allowing Any User to Bypass the Swap Guard — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool. When `MetricOmmSimpleRouter` is the direct caller of `pool.swap()`, the pool sets `sender = msg.sender = router`. The extension therefore checks whether the **router** is allowlisted, not the actual end user. Any unprivileged user can bypass a per-user swap allowlist by routing through the public `MetricOmmSimpleRouter`.

---

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the `sender` argument:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
    extensionData
);
```

`ExtensionCalling._beforeSwap` encodes that value verbatim and dispatches it to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

From the pool's perspective `msg.sender = router`, so `sender = router` is what the extension receives. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Bypass path**: A pool admin who wants to allow their allowlisted users to use the router must add the router address to the allowlist. Once the router is allowlisted, `allowedSwapper[pool][router] = true` passes for every caller of the router regardless of who they are, because the extension never sees the real user's address.

The same identity substitution occurs in multi-hop `exactInput` (line 104) and `exactOutput` (line 165, 220) paths in the router.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, whitelisted market makers, or protocol-controlled accounts) is fully bypassed by any unprivileged user who routes through `MetricOmmSimpleRouter`. The guard that was meant to protect LP assets from unauthorized trading is silently inoperative for all router-mediated swaps. LPs suffer unauthorized adverse selection and fee leakage from actors the pool admin explicitly intended to exclude.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly documented entry point for swaps. Any user who reads the periphery interface will naturally use it. A pool admin who allowlists the router to enable convenient access for their users (a completely expected operational step) immediately opens the pool to all users. No special knowledge, flash loans, or privileged access is required — a single `exactInputSingle` call from any EOA is sufficient.

---

### Recommendation

The pool must forward the **originating user** identity, not the immediate `msg.sender`, to the extension. Two complementary fixes:

1. **Router-side**: Store the real user (`msg.sender` at router entry) in transient storage alongside the existing callback context, then pass it as `callbackData` or a dedicated field so the pool can forward it to extensions.

2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should accept an optional "real sender" encoded in `extensionData` and verify it when present, falling back to the raw `sender` for direct pool calls. The router must populate this field for every hop.

A simpler but less flexible alternative is to document that the allowlist gates the **direct caller of `pool.swap()`** and require pool admins to allowlist the router only when they intend to open the pool to all router users, never when per-user gating is the goal.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
3. Pool admin calls setAllowedToSwap(pool, router, true)  // admin adds router so alice can use it

Attack
──────
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: bob})

5. Router calls pool.swap(bob, zeroForOne, amount, ...)
   → pool.msg.sender = router
   → _beforeSwap(sender=router, ...)
   → extension checks allowedSwapper[pool][router] → true  ✓
   → swap executes, bob receives tokens

6. Bob has successfully swapped on a pool that was supposed to block him.
   Alice's allowlist entry is irrelevant; the router entry is the only one that matters.
```

**Direct call still blocked** (confirming the guard works only for direct callers):
```
7. Bob calls pool.swap(...) directly
   → _beforeSwap(sender=bob, ...)
   → allowedSwapper[pool][bob] → false → revert NotAllowedToSwap ✓
```

The asymmetry between step 6 and step 7 proves the bypass is router-specific and fully reachable by any unprivileged user. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
