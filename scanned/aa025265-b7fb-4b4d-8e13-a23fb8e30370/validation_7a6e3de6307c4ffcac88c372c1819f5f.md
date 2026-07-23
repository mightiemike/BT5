### Title
Router-Mediated Swap Bypasses SwapAllowlist Gate via Wrong Actor Identity — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the **router address**, not the user. If the pool admin allowlists the router (the only way to enable router-mediated swaps on a restricted pool), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Call path — direct swap (correct):**

```
User → MetricOmmPool.swap()
  msg.sender = User
  _beforeSwap(msg.sender=User, ...)
  SwapAllowlistExtension.beforeSwap(sender=User, ...)
  check: allowedSwapper[pool][User]  ✓ correct actor
```

**Call path — router swap (broken):**

```
User → MetricOmmSimpleRouter.exactInputSingle()
  Router → MetricOmmPool.swap()
    msg.sender = Router
    _beforeSwap(msg.sender=Router, ...)
    SwapAllowlistExtension.beforeSwap(sender=Router, ...)
    check: allowedSwapper[pool][Router]  ✗ wrong actor
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When the router calls `pool.swap()`, `sender = router`: [4](#0-3) 

The router stores the real user only in transient storage for its own callback (`_getPayer()`), but never surfaces it to the pool or the extension. The extension has no way to recover the original user.

**The trap for pool admins:** To allow any allowlisted user to swap through the router, the admin must add the router to `allowedSwapper[pool][router]`. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every caller, so any unprivileged user can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the hook passes unconditionally.

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole access-control mechanism for curated pools. A pool admin who allowlists the router to enable normal UX inadvertently opens the pool to every address. Any non-allowlisted user can execute swaps on a pool that was intended to be restricted (e.g., KYC-gated, institution-only, or whitelist-only pools). This is a complete policy bypass of the allowlist invariant.

---

### Likelihood Explanation

The router is the standard user-facing entry point for swaps. Any pool admin who wants their allowlisted users to be able to use the router must allowlist the router address. This is a natural and expected configuration step, making the bypass reachable in any production deployment that combines `SwapAllowlistExtension` with `MetricOmmSimpleRouter`.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **ultimate economic actor**, not the immediate `msg.sender` of `pool.swap()`. Two concrete options:

1. **Pass the real payer through `extensionData`**: The router encodes the real user in `extensionData`; the extension reads and verifies it. This requires a convention between router and extension.
2. **Check `recipient` instead of (or in addition to) `sender`**: For single-hop swaps the recipient is often the real user, though this breaks for multi-hop paths where intermediate recipients are the router itself.

The cleanest fix is option 1: the router explicitly encodes `msg.sender` (the real user) in `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `extensionData` is non-empty, falling back to `sender` for direct calls.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true      // alice is the only allowed swapper
  allowedSwapper[pool][router] = true     // admin adds router so alice can use the UI

Attack (bob, not allowlisted):
  bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  router calls pool.swap(bob, ...)        // msg.sender = router
  pool calls extension.beforeSwap(sender=router, ...)
  check: allowedSwapper[pool][router] == true  → PASS
  bob receives tokens from the restricted pool
```

Direct assertion: `allowedSwapper[pool][bob]` is `false`, yet bob successfully swaps because the hook checked `allowedSwapper[pool][router]` instead.

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
