### Title
SwapAllowlistExtension checks router address as `sender` instead of actual user, enabling allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, the pool sees `sender` = router address, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps for their permitted users inadvertently opens the allowlist to every user who routes through the same router.

---

### Finding Description

**Root cause — wrong actor bound in the extension check.**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][directCallerOfPool]`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) is used, the router is the direct caller of `pool.swap`: [4](#0-3) 

So the extension sees `sender` = `MetricOmmSimpleRouter` address, not the end user. The allowlist check becomes `allowedSwapper[pool][router]`.

**The dilemma the pool admin faces:**

| Admin configuration | Allowed user via router | Non-allowed user via router |
|---|---|---|
| Router NOT allowlisted | ❌ blocked (usability broken) | ✅ blocked |
| Router allowlisted | ✅ allowed | ❌ **also allowed — bypass** |

There is no configuration that simultaneously allows permitted users to use the router and blocks non-permitted users. The stored allowlist entry for the router address is used as a static gate instead of dynamically resolving the actual initiating user — the exact same class of flaw as the external report, where a stored maximum value is substituted for a dynamically computed proportional value.

---

### Impact Explanation

**High.** Any non-allowlisted user can bypass a curated pool's swap allowlist by routing through `MetricOmmSimpleRouter`. Once the router is allowlisted (a necessary step for permitted users to use the standard periphery), the allowlist provides zero protection against router-mediated swaps. Pools intended for KYC'd participants, institutional traders, or other restricted sets are fully open to arbitrary swappers via the router, breaking the core access-control invariant the extension is designed to enforce.

---

### Likelihood Explanation

**Medium.** The bypass requires the pool admin to allowlist the router. This is a natural and expected configuration: any pool admin who wants their permitted users to access the standard periphery must allowlist the router. The flaw is latent in every such deployment and is triggered by any unprivileged user who calls the router against the pool.

---

### Recommendation

The extension must resolve the actual initiating user rather than the direct pool caller. Two viable approaches:

1. **Router passes user identity in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. The pool must also verify the router is a trusted forwarder so the identity cannot be spoofed by an arbitrary caller.
2. **Check `sender` only for direct pool calls; require a separate allowlist entry for router-mediated calls keyed by the user address embedded in `extensionData`**: This preserves backward compatibility while closing the bypass.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)      // needed so alice can use the router
4. Bob (non-KYC'd) calls router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient=bob, ...)  →  pool.msg.sender = router
6. Pool calls _beforeSwap(sender=router, ...)
7. SwapAllowlistExtension checks allowedSwapper[pool][router] == true  →  passes
8. Bob's swap executes on the curated pool despite not being allowlisted.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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
