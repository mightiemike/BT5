### Title
SwapAllowlistExtension Gates on the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, any unprivileged address can bypass the curated allowlist by routing through the same public router.

---

### Finding Description

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension is the pool): [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the extension receives `sender = address(router)`, not the actual user. The allowlist check becomes `allowedSwapper[pool][router]`. If the pool admin allowlists the router (the natural step to let legitimate users use the standard periphery), the check passes for **every caller of the router**, regardless of whether they are individually allowlisted.

The router itself performs no identity check on behalf of the pool's allowlist — it simply forwards `params.recipient` as the output destination and calls `pool.swap` as itself.

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC-verified users, institutional counterparties) is fully bypassed by any unprivileged user who routes through `MetricOmmSimpleRouter`. The attacker can execute swaps in the restricted pool, receiving output tokens, without ever being individually allowlisted. This constitutes a direct admin-boundary break: an unprivileged path defeats a pool-admin-configured access control, with fund-impacting consequences (unauthorized trading in a curated pool).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, documented user-facing entry point for swaps. A pool admin who wants their allowlisted users to be able to use the router must allowlist the router address — there is no other mechanism. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges. The attacker only needs to call a public router function with the target pool address.

---

### Recommendation

The allowlist must gate the economically relevant actor — the address that initiated the swap — not the intermediary contract. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.
2. **Check `recipient` instead of (or in addition to) `sender`**: For single-hop swaps the recipient is the user-controlled address. This is imperfect for multi-hop paths where intermediate recipients are the router itself.
3. **Require direct pool interaction for allowlisted pools**: Document and enforce that pools using `SwapAllowlistExtension` must not allowlist the router, and allowlisted users must call `pool.swap` directly. This is operationally fragile but avoids code changes.

The cleanest fix is option 1: the router should encode `msg.sender` into `extensionData` and the extension should decode and verify it, so the allowlist always sees the true initiating user.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured as a before-swap hook.
2. Pool admin calls:
       extension.setAllowedToSwap(pool, address(router), true)
   (intending to let their KYC'd users use the router)
3. Attacker (not individually allowlisted) calls:
       router.exactInputSingle({
           pool:      <curated pool>,
           tokenIn:   token0,
           tokenOut:  token1,
           zeroForOne: true,
           amountIn:  X,
           recipient: attacker,
           ...
       })
4. Execution path:
       router.exactInputSingle()
         → pool.swap(recipient=attacker, ...)   [msg.sender = router]
           → _beforeSwap(sender=router, ...)
             → extension.beforeSwap(sender=router, ...)
               → allowedSwapper[pool][router] == true  ✓  (no revert)
         → token1 transferred to attacker
5. Attacker receives output tokens from the curated pool without being
   individually allowlisted. The allowlist is completely bypassed.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
