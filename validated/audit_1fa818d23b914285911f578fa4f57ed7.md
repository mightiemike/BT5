### Title
`SwapAllowlistExtension` gates on the router address instead of the end user, enabling full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. A pool admin who allowlists the router to support normal routing inadvertently grants every user — including explicitly blocked ones — the ability to swap on the curated pool.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user goes through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

So `sender` arriving at the extension is the **router address**, not the end user. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

This creates two mutually exclusive broken states:

| Router allowlisted? | Effect |
|---|---|
| Yes | Every user — including explicitly blocked ones — bypasses the allowlist by routing through the router |
| No | Every allowlisted user is blocked from using the router; core swap functionality is broken |

---

### Impact Explanation

**Critical/High — allowlist bypass with direct fund impact.**

A curated pool (e.g., KYC-gated, institution-only) relies on `SwapAllowlistExtension` to restrict who can trade. If the router is allowlisted (the only way to support normal periphery usage), any unpermissioned address can call `router.exactInputSingle()` and swap freely. The pool's curation guarantee is completely voided. Unauthorized users can drain one side of the pool at oracle-derived prices, causing direct LP principal loss.

---

### Likelihood Explanation

**High.** The router is the canonical user-facing entrypoint documented in the periphery. Any pool that enables `SwapAllowlistExtension` and also wants users to use the router must allowlist the router — triggering the bypass automatically. No special preconditions, no privileged access, no non-standard tokens required.

---

### Recommendation

The extension must verify the **economic actor**, not the immediate caller. Two options:

1. **Pass the original initiator through the router.** Have the router encode `msg.sender` in `extensionData` and have the extension decode and verify it. This requires a trust assumption that the extension only accepts data from known routers.

2. **Check `sender` against a router registry and fall back to the payer stored in transient storage.** The router already stores the payer in transient storage (`_getPayer()`); expose it or pass it as a verified field.

3. **Simplest safe fix:** document that `SwapAllowlistExtension` is incompatible with the router and enforce this at pool creation time via `ValidateExtensionsConfig`, or add a separate router-aware allowlist that checks both the router address and a caller-supplied identity verified by signature.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, userA, true)   // userA is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // needed for router support
   // userB is NOT allowlisted
4. userB calls router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(recipient, ...) with msg.sender = router
   → pool calls _beforeSwap(sender=router, ...)
   → extension checks allowedSwapper[pool][router] == true  ✓
   → swap executes successfully for userB
5. userB, who should be blocked, has swapped on the curated pool.
   The allowlist is completely bypassed.
``` [5](#0-4) [6](#0-5)

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
