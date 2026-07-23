### Title
`SwapAllowlistExtension` gates on the router address instead of the actual user when swaps are routed through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router contract, not the user. The allowlist therefore gates on the router's address rather than the actual swapper's address. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every user — including those explicitly excluded from the allowlist — can bypass the gate by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
          msg.sender = router
     → _beforeSwap(msg.sender=router, recipient, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          checks allowedSwapper[pool][router]   ← router, not user
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original user's address: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Two broken states result from this mismatch:**

1. **Allowlist bypass (primary impact):** The pool admin allowlists the router so that router-mediated swaps are possible. Because the extension sees only the router address, every user — including those the admin explicitly excluded — can swap by calling the router. The per-user allowlist is completely ineffective.

2. **Allowlisted users locked out of the router:** If the pool admin does not allowlist the router, individually allowlisted users cannot use the router at all, even though they are permitted. They are forced to call `pool.swap` directly, which is not the supported UX path.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners, or protocol-controlled addresses) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker needs no special privilege: they simply call the public router. The pool's LP assets are exposed to swaps from actors the pool admin explicitly intended to block, which can result in direct loss of LP principal through adverse selection or policy-violating trades.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin who deploys `SwapAllowlistExtension` and wants users to be able to swap through the router must allowlist the router. Once that is done, the bypass is unconditional and requires no special setup by the attacker. The trigger is a normal `exactInputSingle` call from any EOA.

---

### Recommendation

The pool must forward the original user's address through the router so the extension can gate on the correct actor. One approach is to have the router pass the original `msg.sender` as `callbackData` and have the pool expose it as the canonical `sender` to extensions. Alternatively, `SwapAllowlistExtension` can be extended to accept a signed or verified user identity from `extensionData`. The simplest fix consistent with the existing architecture is to have the router store the original caller in transient storage (as it already does for the payer) and expose it via a standard interface that the pool reads and forwards as `sender` to hooks.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   (admin must do this to allow router swaps)
  allowedSwapper[pool][alice]  = false  (alice is explicitly blocked)

Attack:
  alice calls router.exactInputSingle({pool: pool, ...})
  router calls pool.swap(...)  →  msg.sender = router
  pool calls _beforeSwap(sender=router, ...)
  SwapAllowlistExtension checks allowedSwapper[pool][router] == true  → passes
  alice's swap executes despite being on the blocklist
```

The existing unit test `test_allowedSwapSucceeds` in `FullMetricExtension.t.sol` allowlists `callers[0]` (a `TestCaller` contract) and calls the pool directly — it never exercises the router path, so the bypass is untested. [6](#0-5)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
