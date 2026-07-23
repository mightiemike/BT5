### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router**, not the user. If the pool admin allowlists the router (the natural step to enable router-based swaps for allowlisted users), every unprivileged user can bypass the allowlist entirely by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender` value: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router**, so `sender` delivered to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The analogous wrong-actor binding: just as the PaymentSplitter `receive` function called `_emit_payee_added_event` (the wrong hook for the action), `SwapAllowlistExtension.beforeSwap` checks the wrong actor (the router intermediary instead of the originating user) for the swap action.

---

### Impact Explanation

A pool admin who wants allowlisted users to be able to trade through the standard periphery router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **any** address — including addresses the admin explicitly never allowlisted — can bypass the gate by calling `exactInputSingle` or `exactInput` on the router. The allowlist provides zero protection for router-mediated swaps. This is an admin-boundary break: the pool admin's configured access-control policy is bypassed by an unprivileged path (the public router).

---

### Likelihood Explanation

The `SwapAllowlistExtension` NatSpec states it "Gates `swap` by swapper address, per pool." [5](#0-4) 

A pool admin who deploys this extension to create a curated pool will naturally also allowlist the router so that their approved users can trade through the standard periphery. The extension contains no documentation warning that allowlisting the router opens the gate to all users. The bypass is therefore reachable on any production curated pool that supports router-based swaps.

---

### Recommendation

The extension must gate on the **originating user**, not the intermediary. Two viable approaches:

1. **Trusted `extensionData` field**: require the router to ABI-encode the original `msg.sender` into `extensionData` and have the extension decode and verify it. This requires a coordinated router change and trust that the bytes are not forged by the user.
2. **Separate allowlist entry for router-mediated swaps**: document clearly that allowlisting the router opens the gate to all users, and require pool admins to use `allowAllSwappers` instead if router access is desired — removing the false sense of security the per-address allowlist provides.

The cleanest fix is for the router to pass the originating caller through a dedicated field (e.g., a structured `extensionData` prefix) and for the extension to decode and check that field when `sender` is a known router.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)      // alice is approved
  admin calls setAllowedToSwap(pool, router, true)     // router allowlisted so alice can use it

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: charlie, ...})

  router calls:
    pool.swap(charlie, zeroForOne, amount, limit, "", extensionData)
    // msg.sender inside pool = router

  pool calls:
    extension.beforeSwap(sender=router, recipient=charlie, ...)
    // extension checks allowedSwapper[pool][router] == true  ✓
    // charlie's swap proceeds — allowlist bypassed
```

Charlie successfully swaps in the curated pool without ever being allowlisted, because the extension evaluated the router's allowlist entry rather than Charlie's.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
