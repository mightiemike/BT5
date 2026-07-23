### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of `MetricOmmPool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the end-user. If the pool admin allowlists the router (the natural action to enable router-mediated swaps for permitted users), every unpermissioned user can bypass the allowlist by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ..., extensionData)
              [msg.sender in pool = router address]
         → MetricOmmPool._beforeSwap(msg.sender=router, ...)
         → ExtensionCalling._beforeSwap(sender=router, ...)
         → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              checks: allowedSwapper[pool][router]   ← router, not user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When the swap enters through `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

The router stores the real user only in transient payment context (`_getPayer()`), which is never surfaced to the extension. The extension therefore evaluates `allowedSwapper[pool][router]` — a single bit that covers every user who routes through the router — rather than the individual end-user identity the allowlist was designed to gate.

---

### Impact Explanation

A pool admin who wants allowlisted users to be able to use the standard router must add the router to `allowedSwapper`. Once the router is allowlisted, **any** address — including addresses the admin explicitly never allowlisted — can call `router.exactInputSingle()` and execute swaps on the curated pool. The allowlist protection is completely nullified for the router path. LP funds in the curated pool are exposed to unauthorized counterparties, which can cause direct loss of LP principal through adverse selection or policy-violating trades on pools designed for restricted counterparties.

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router, which is the natural and expected action when deploying a curated pool that should be accessible via the standard periphery. Without allowlisting the router, even legitimately allowlisted users cannot use the router, making the allowlist operationally unusable for any router-mediated flow. The admin is therefore forced into a choice between usability (allowlist the router, bypass opens) and security (don't allowlist the router, legitimate users are also blocked). Any production curated pool that intends to support router-mediated swaps will trigger this bypass.

---

### Recommendation

The extension must check the **economic actor** — the address that initiated the swap and will pay for it — not the intermediate dispatcher. Two complementary fixes:

1. **Pass the original initiator through the extension payload.** The router should encode `msg.sender` (the real user) into `extensionData` and the extension should decode and check it. This requires a convention between the router and the extension.

2. **Alternatively, check `recipient` instead of `sender` when the pool is called via a router.** The recipient is set by the user and is harder to spoof, though it is not the payer.

The cleanest fix is for the pool to expose the original initiator (e.g., via a dedicated field in the swap call or a standardized `extensionData` prefix), so that allowlist extensions can always gate the true economic actor regardless of which periphery contract relays the call.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured in beforeSwap order
  allowedSwapper[pool][alice] = true          // alice is KYC'd
  allowedSwapper[pool][bob]   = false         // bob is NOT allowlisted
  allowedSwapper[pool][router] = true         // admin adds router so alice can use it

Attack (bob, unpermissioned):
  router.exactInputSingle({
      pool:      pool,
      recipient: bob,
      zeroForOne: true,
      amountIn:  X,
      ...
  })
  // router calls pool.swap(bob, true, X, ...) with msg.sender = router
  // pool calls _beforeSwap(sender=router, ...)
  // extension checks allowedSwapper[pool][router] → true  ✓ (bypass!)
  // swap executes; bob receives output tokens from the curated pool

Result:
  bob, who was never allowlisted, successfully swaps on the curated pool.
  The allowlist protection is fully bypassed for any user routing through
  the standard MetricOmmSimpleRouter.
```

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
