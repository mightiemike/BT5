### Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router address**, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps, every user — including non-allowlisted ones — bypasses the access gate entirely.

---

### Finding Description

**Root cause — wrong identity in the hook argument:**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

**Router call path — actual user address is never forwarded:**

`MetricOmmSimpleRouter.exactInputSingle` stores the real user in transient callback context (`msg.sender`) but calls `pool.swap()` directly, making the pool see `msg.sender = router`: [4](#0-3) 

The real user address is only used inside `_setNextCallbackContext` for payment settlement; it is never passed to the pool's `swap()` arguments and therefore never reaches the extension.

**The bypass:**

A pool admin who wants allowlisted users to be able to use the router must allowlist the router address itself. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[pool][sender]` evaluates to `true` for **every** caller of the router, because `sender` is always the router address regardless of who initiated the transaction. The allowlist is completely neutralised for all router-mediated swaps.

The same flaw exists in the multi-hop `exactInput` path: [5](#0-4) 

and in the `exactOutput` recursive callback path: [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (KYC'd users, institutional LPs, whitelisted market makers) is fully open to any address that routes through `MetricOmmSimpleRouter`. Unauthorized traders can execute swaps against the pool's LP positions, causing direct LP principal loss in a pool that was designed to be access-controlled. The pool's core invariant — "only allowlisted addresses may swap" — is broken for the primary public entry point.

---

### Likelihood Explanation

**Medium.** The trigger requires the pool admin to allowlist the router address. This is a natural, expected action: allowlisted users will attempt to use the router (the standard UX path), find their swaps reverting because the router is not allowlisted, and the admin will add the router to unblock them. The admin has no mechanism to say "allow user X through the router" — the only granularity available is the router address itself, which grants access to all router callers. The mistake is structurally induced by the design.

---

### Recommendation

The extension must check the **originating user**, not the intermediary contract. Two viable approaches:

1. **Pass the real caller through `extensionData`:** The router encodes `msg.sender` into `extensionData` for each hop; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Add a `realSender` field to the pool's swap interface:** The pool accepts an explicit `realSender` argument (verified against `msg.sender` or a trusted router registry) and forwards it to extensions instead of `msg.sender`.

Until fixed, pool admins must be warned that allowlisting the router address is equivalent to disabling the allowlist for all router users.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
  admin calls extension.setAllowedToSwap(pool, alice, true)
  admin calls extension.setAllowedToSwap(pool, router, true)   // to unblock router UX

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: T0, tokenOut: T1, ...})

  router calls:
    pool.swap(recipient=bob, ...)   // msg.sender = router

  pool calls:
    _beforeSwap(sender=router, ...)

  extension checks:
    allowedSwapper[pool][router] == true  → passes

  Result: bob's swap executes against the pool's LP positions.
          The allowlist never checked bob's address.
``` [3](#0-2) [1](#0-0) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
