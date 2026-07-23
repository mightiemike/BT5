### Title
SwapAllowlistExtension Gates the Router Address Instead of the Original User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router, not the original user. If the pool admin allowlists the router (the only way to permit router-mediated swaps on a curated pool), the allowlist is silently bypassed for every user who routes through it.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
              → MetricOmmPool._beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        checks: allowedSwapper[pool][router]   // ← router, not user
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When the user goes through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` directly, so `sender` received by the extension is the router address, not the original user: [4](#0-3) 

The pool admin faces an impossible choice:
- **Do not allowlist the router** → every router-mediated swap reverts for all users, including legitimately allowlisted ones.
- **Allowlist the router** → `allowedSwapper[pool][router] == true`, so the guard passes for *any* caller who routes through the router, regardless of whether that caller is on the allowlist.

There is no mechanism in the router to forward the original `msg.sender` to the pool as the `sender` identity. The multi-hop `exactInput` path has the same flaw for every hop after the first, where the payer is `address(this)` (the router): [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The bypassing user can execute swaps at oracle-derived prices, extracting value from LP positions that were deposited under the assumption that only allowlisted counterparties would trade against them. This is a direct loss of LP principal and fee revenue attributable to trades that should have been blocked.

---

### Likelihood Explanation

The router is the primary user-facing swap entrypoint documented and deployed by the protocol. Any pool admin who enables a swap allowlist and also wants to support normal router-mediated swaps must allowlist the router — the exact configuration that opens the bypass. The trigger requires no special privilege: any EOA can call `router.exactInputSingle` with a valid pool address. The condition is reachable on every curated pool that has the router allowlisted.

---

### Recommendation

The `sender` identity forwarded to extensions must represent the original economic actor, not the intermediary contract. Two complementary fixes:

1. **In the router:** pass the original `msg.sender` as an authenticated field inside `extensionData` (signed or verified via transient storage), and have `SwapAllowlistExtension` decode and verify it.
2. **In the extension:** document explicitly that `sender` is the direct caller of `pool.swap`, not the end user, and require pool admins to allowlist routers only when the allowlist is not intended to gate individual users.

The cleaner fix is option 1: the router stores the original caller in transient storage (as it already does for the payer), and the extension reads it from a well-known slot rather than trusting the `sender` argument.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as extension1
  admin calls extension.setAllowedToSwap(pool, router, true)   // to enable router swaps
  alice = allowlisted user
  bob   = non-allowlisted user

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=bob, ...)   // msg.sender at pool = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
    → swap executes — bob receives output tokens

Expected:
  SwapAllowlistExtension should check allowedSwapper[pool][bob] == false → revert NotAllowedToSwap

Actual:
  The check passes because the router is allowlisted; bob's swap settles at oracle price,
  extracting value from LP positions that were deposited under a curated-counterparty assumption.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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
```
