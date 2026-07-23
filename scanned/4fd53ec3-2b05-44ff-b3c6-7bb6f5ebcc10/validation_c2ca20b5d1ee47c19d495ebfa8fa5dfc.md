### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address, not the actual end user. If the pool admin allowlists the router (required for any router-based swap to work), every user on the network can bypass the allowlist by routing through the router.

---

### Finding Description

The `SwapAllowlistExtension` is designed to restrict which addresses may swap on a curated pool. Its `beforeSwap` hook receives a `sender` argument and checks it against the per-pool allowlist: [1](#0-0) 

`msg.sender` inside the extension is the pool (correct), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`: [2](#0-1) 

`_beforeSwap` is called from `MetricOmmPool.swap()` with `msg.sender` of the pool call as `sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

At this point `msg.sender` to the pool is the **router address**, so `sender` forwarded to the extension is the **router**, not the actual end user. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`.

The pool admin faces an impossible choice:

| Admin decision | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user on the network can bypass the allowlist via the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

Any user can swap on a pool protected by `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`. The allowlist guard silently fails open for all router-mediated swaps. This breaks the core curation invariant of the pool and allows unauthorized users to trade, potentially draining LP assets or executing swaps the pool was designed to restrict.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, publicly deployed periphery entrypoint. Any user who knows the pool address can call `exactInputSingle` or `exactInput` against it. No special privilege, flash loan, or multi-step setup is required. The only precondition is that the pool admin has allowlisted the router (which is necessary for any legitimate router-based swap to function). Likelihood is **High**.

---

### Recommendation

The extension must check the **economic actor** (the end user), not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original `msg.sender` through the router**: The router should encode the real user in `extensionData` and the extension should decode and verify it. This requires a trusted router assumption.
2. **Check `sender` only for direct pool calls; require the router to forward the real user identity**: Modify the router to pass the real user as `recipient` or via a signed payload, and update the extension to verify it.

The cleanest fix is to have the router pass the real user's address in `extensionData` and have the extension decode and gate on that address when `sender` is a known router, or to redesign the hook signature so the real originator is always available.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, alice, true)       // alice is allowed
  - Admin calls setAllowedToSwap(pool, router, true)      // router must be allowed for alice to use it
  - bob is NOT in the allowlist

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
     → msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. ExtensionCalling encodes sender=router and calls SwapAllowlistExtension.beforeSwap(router, ...)
  5. Extension checks: allowedSwapper[pool][router] == true  ✓
  6. bob's swap executes successfully — allowlist bypassed
``` [5](#0-4) [4](#0-3) [3](#0-2)

### Citations

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
