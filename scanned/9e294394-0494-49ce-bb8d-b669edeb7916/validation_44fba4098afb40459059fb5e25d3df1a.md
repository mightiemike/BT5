### Title
SwapAllowlistExtension gates the router address instead of the end user, enabling allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to enable router-mediated swaps for their curated users, every unprivileged user can bypass the allowlist by calling through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
     → MetricOmmPool.swap()
     → ExtensionCalling._beforeSwap(msg.sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the pool's caller — the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` stores the original user only in transient callback context (for token payment), but never passes it to `pool.swap()`: [4](#0-3) 

The router's `msg.sender` (the real user) is invisible to the extension. The extension can only see the router's address.

**Two broken invariants result:**

1. **Bypass path:** If the pool admin allowlists the router (`allowedSwapper[pool][router] = true`) to let their curated users trade via the router, every unprivileged user can also trade by calling `exactInputSingle` through the same router. The router is a public, permissionless contract.

2. **Broken functionality:** If the pool admin does *not* allowlist the router, allowlisted users cannot use the router at all — their individual allowlist entries are keyed to their own address, but the extension sees the router's address and reverts.

The pool admin has no way to simultaneously allow router-mediated swaps for their allowlisted users and block non-allowlisted users from doing the same.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise approved addresses loses that restriction entirely the moment the router is allowlisted. Any address can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool. This is a direct policy bypass with fund-impacting consequences: the pool's LP providers deposited under the assumption that only approved counterparties would trade against them.

---

### Likelihood Explanation

The pool admin must allowlist the router to enable the supported periphery path for their approved users. This is the natural, expected configuration for any curated pool that wants to support the standard router UX. The bypass is therefore reachable on any production curated pool that follows the intended integration pattern.

---

### Recommendation

The extension must gate the **economically relevant actor** — the end user — not the intermediary router. Two viable approaches:

1. **Pass the original caller through the router.** Have `MetricOmmSimpleRouter` accept an explicit `swapper` parameter (or read it from `msg.sender`) and forward it to the pool via `extensionData`. The extension decodes it from `extensionData` and checks it instead of `sender`.

2. **Check `sender` only when it is not a known router.** The extension could maintain a registry of trusted routers; when `sender` is a router, it decodes the real user from `extensionData`.

The simplest correct fix is option 1: the router passes `msg.sender` in `extensionData`, and the extension decodes and checks that address against the allowlist.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is KYC'd
  allowedSwapper[pool][router] = true         // admin enables router for alice

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})
    → pool.swap(bob, ...) with msg.sender = router
    → extension checks allowedSwapper[pool][router] → true → PASSES
    bob swaps successfully against the curated pool
```

Direct call by bob would revert:
```
  pool.swap(bob, ...) with msg.sender = bob
  → extension checks allowedSwapper[pool][bob] → false → REVERTS
```

The bypass is available to any caller of the public router whenever the router address is in the pool's allowlist.

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
