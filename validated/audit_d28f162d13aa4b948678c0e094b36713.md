### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. The pool always passes its own `msg.sender` as `sender`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If the pool admin allowlists the router (which is required for any router-mediated swap to succeed), every unprivileged user can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

**Pool passes `msg.sender` as `sender` to the extension:**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with its own `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` argument to every configured extension: [2](#0-1) 

**The extension checks that forwarded `sender`:**

`SwapAllowlistExtension.beforeSwap` gates on `allowedSwapper[pool][sender]`: [3](#0-2) 

**The router calls `pool.swap()` directly, making itself `msg.sender`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` — the actual end user (`msg.sender` of the router call) is stored only in the transient callback context for payment, never forwarded to the pool as the swap initiator: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Result:** When a swap goes through the router, the extension sees `sender = router_address`. The pool admin must allowlist the router address for any router-mediated swap to succeed. Once the router is allowlisted, the check `allowedSwapper[pool][router_address]` passes for **every caller** of the router, regardless of whether that caller is individually allowlisted.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. The router is a public, permissionless contract. Any address — including addresses the pool admin explicitly excluded — can execute swaps on the curated pool by calling `exactInputSingle` or any other router entry point. This breaks the core access-control invariant of the allowlist extension and constitutes a broken core pool functionality / admin-boundary break with direct fund-impact consequences (disallowed parties trade on a pool that should be closed to them).

---

### Likelihood Explanation

The router is the standard, documented periphery entry point for swaps. Pool admins who deploy a `SwapAllowlistExtension` pool and want legitimate allowlisted users to be able to use the router **must** add the router to the allowlist. The moment they do, the allowlist is effectively open to everyone. The trigger requires no special privilege, no flash loan, and no unusual token — only a call to a public router function.

---

### Recommendation

The extension must gate the **end user**, not the intermediary. Two sound approaches:

1. **Pass the real initiator through `extensionData`:** Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. This requires the router to be a trusted forwarder and the extension to validate that the call came from a known router.

2. **Check `tx.origin` as a fallback identity for router paths:** Acceptable only if the threat model explicitly excludes contract callers; otherwise it introduces its own bypass surface.

3. **Require direct pool calls for allowlisted pools:** Document that `SwapAllowlistExtension` pools must not allowlist the router, and that allowlisted users must call the pool directly. This is operationally fragile but avoids code changes.

The cleanest fix is option 1: the router encodes `msg.sender` into `extensionData`, and the extension, when it recognises a trusted-router prefix in `extensionData`, checks the decoded user address instead of the raw `sender`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowed
  pool admin calls setAllowedToSwap(pool, router, true)      // router must be allowed for alice to use it
  bob is NOT in the allowlist

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)          // msg.sender = router
    → pool calls _beforeSwap(router, ...)
    → SwapAllowlistExtension.beforeSwap(router, ...)
        allowedSwapper[pool][router] == true          // passes!
    → swap executes for bob
```

Bob, an explicitly excluded address, successfully swaps on the curated pool. The allowlist is fully bypassed. [6](#0-5) [7](#0-6) [8](#0-7)

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
