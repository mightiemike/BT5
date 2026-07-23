### Title
`SwapAllowlistExtension` gates the router address instead of the real user, allowing any caller to bypass a per-user swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` (the first argument the pool passes, which equals `msg.sender` of the `pool.swap()` call) against the per-pool allowlist. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension sees `sender = router`, not the actual user. A pool admin who allowlists the router to let their approved users trade through the standard interface simultaneously grants every unpermissioned user the same access, fully defeating the allowlist guard.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. In every router path the pool receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates two mutually exclusive broken states:

1. **Router not allowlisted** — allowlisted users cannot use the standard router interface; their swaps revert with `NotAllowedToSwap` even though they are individually permitted. Core swap functionality is broken for the intended audience.

2. **Router allowlisted** (the only fix available to the admin) — every unpermissioned user can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool, because the extension sees `sender = router` and the router is in the allowlist. The per-user gate is completely bypassed.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or a private market) can be freely traded against by any address through the public router. Unauthorized swappers extract value from LP positions at oracle-anchored prices, causing direct loss of LP principal. This satisfies the "broken core pool functionality causing loss of funds" and "admin-boundary break" impact criteria.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point documented and deployed for the protocol. Any user who reads the router interface can route through it. No special privilege, flash loan, or unusual token behavior is required. The trigger is a single standard `exactInputSingle` call.

---

### Recommendation

The pool should pass the original end-user identity through the swap call so the extension can gate on it. Two concrete options:

1. **Add a `swapper` field to `swap()`** — the pool accepts an explicit `swapper` address (defaulting to `msg.sender`) and passes it as `sender` to extensions. The router forwards `msg.sender` in this field.

2. **Check `sender` in the router's callback context** — the router already stores the original `msg.sender` in transient storage (`_setNextCallbackContext(..., msg.sender, ...)`). Expose it via a view so extensions can read the true initiator.

Until fixed, pool admins should not deploy `SwapAllowlistExtension` on pools that are also accessible through `MetricOmmSimpleRouter` if per-user access control is a security requirement.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension (beforeSwap order = extension 1)
  admin allowlists alice: allowedSwapper[pool][alice] = true
  admin also allowlists router so alice can use it: allowedSwapper[pool][router] = true

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., amountIn: X})

  Execution path:
    router.exactInputSingle  (msg.sender = bob)
      → pool.swap(...)       (msg.sender = router)
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, bob receives output tokens

Result: bob, who is not in the allowlist, successfully swaps against the pool.
``` [5](#0-4) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
