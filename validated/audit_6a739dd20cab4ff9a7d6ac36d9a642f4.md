### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
When a pool is guarded by `SwapAllowlistExtension` and a user swaps through `MetricOmmSimpleRouter`, the extension receives the router's address as `sender` rather than the actual user's address. If the pool admin allowlists the router to support router-mediated swaps, every unpermissioned user can bypass the allowlist entirely by routing through the router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then gates on that `sender` value: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So from the pool's perspective `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`. The same misbinding occurs for `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

This creates two mutually exclusive failure modes:

1. **Router not allowlisted**: Allowlisted users cannot swap through the router at all — the extension reverts with `NotAllowedToSwap` because the router's address is not in the allowlist. Core router functionality is broken for curated pools.
2. **Router allowlisted** (the natural fix a pool admin would apply): `allowedSwapper[pool][router] = true`, so the check passes for every call that arrives via the router — regardless of who the actual user is. The allowlist is completely bypassed.

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router to restore router-mediated swap support inadvertently opens the pool to every unpermissioned address. Any user can call `MetricOmmSimpleRouter.exactInputSingle()` and trade on a pool that was intended to be restricted. This is a direct loss of the curation policy and, depending on pool design, can expose LP funds to adversarial trading that the allowlist was meant to prevent.

### Likelihood Explanation

Medium. The scenario requires a pool to be configured with `SwapAllowlistExtension` and the pool admin to allowlist the router. Both steps are natural and expected: `SwapAllowlistExtension` is a first-party production extension, and the router is the canonical user-facing swap entrypoint. A pool admin who discovers that allowlisted users cannot use the router will allowlist the router as the obvious remediation, unknowingly enabling the bypass.

### Recommendation

The `sender` forwarded to extensions must represent the economic actor, not the intermediary contract. Two complementary fixes:

1. **In the router**: Store the original `msg.sender` in transient storage alongside the payer and pass it as a separate field in `extensionData` so extensions can recover the true initiator.
2. **In `SwapAllowlistExtension`**: Decode the true initiator from `extensionData` when the direct `sender` is a known router, or require the pool to pass the original user address through a dedicated field in the swap call.

Alternatively, document that `SwapAllowlistExtension` only gates direct pool callers and is incompatible with router-mediated flows, and provide a separate extension that reads the true initiator from transient storage.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin: setAllowedToSwap(pool, alice, true)       // alice is the intended gated user
  pool admin: setAllowedToSwap(pool, router, true)      // admin adds router to support router swaps

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap()                          // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (passes)
      → swap executes for bob

Result: bob trades on a pool he is not allowlisted for.
        The allowlist is fully bypassed for any user who routes through the router.
``` [6](#0-5) [7](#0-6) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
