### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any caller to bypass the swap allowlist when the router is allowlisted — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, not the actual end-user. If the pool admin allowlists the router (a natural step to let allowlisted users reach the pool via the router), every user — including non-allowlisted ones — can bypass the guard by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The router has no access control of its own — any address can call it. Therefore, the allowlist check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. If the pool admin adds the router to the allowlist (the only way to let allowlisted users reach the pool via the router), the guard is silently open to every user on the network.

The same structural problem exists for multi-hop `exactInput` (intermediate hops use `address(this)` as payer, so the router address is again what the extension sees) and for `exactOutput` / `exactOutputSingle`. [5](#0-4) 

---

### Impact Explanation

The `SwapAllowlistExtension` is the primary mechanism for restricting who may trade against a pool — for example, to enforce KYC/AML compliance, to limit a pool to a set of trusted market-makers, or to prevent MEV bots from extracting LP value. Once the router is allowlisted (the only way to let legitimate users reach the pool through the standard periphery), the guard is effectively disabled for all callers. Non-allowlisted actors can execute real swaps, receiving pool output tokens and paying input tokens at oracle-derived prices. LPs bear the full economic exposure of those trades without the protection the allowlist was meant to provide, including potential MEV extraction and regulatory non-compliance.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router — a routine and expected configuration step for any pool that wants its allowlisted users to use the standard periphery. The admin has no reason to suspect this opens the gate to everyone, because the `isAllowedToSwap` view function and the `setAllowedToSwap` setter both operate on individual user addresses, giving no indication that adding the router has pool-wide consequences. The bypass is then reachable by any unprivileged user with no further preconditions.

---

### Recommendation

The extension must resolve the actual end-user identity, not the immediate caller of `pool.swap`. Two complementary fixes:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address instead of (or in addition to) `sender`.

2. **Check both `sender` and a decoded user field**: The extension can require that either the direct caller or the user embedded in `extensionData` is allowlisted, giving the pool admin explicit control over which path is trusted.

Alternatively, document clearly that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and provide a separate router-aware allowlist extension that decodes the real user from `extensionData`.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // Alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // enable router for Alice

Attack
──────
4. Charlie (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., amountIn: X})
   → router calls pool.swap(recipient, ...) with msg.sender = router
   → pool calls extension.beforeSwap(sender=router, ...)
   → extension checks allowedSwapper[pool][router] → TRUE
   → swap executes; Charlie receives output tokens

5. Charlie has bypassed the KYC allowlist with zero privileged access.
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
