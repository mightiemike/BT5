### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against the per-pool allowlist. However, `sender` is sourced from `msg.sender` at the pool level — which is the **router contract**, not the original user — when a swap is routed through `MetricOmmSimpleRouter`. Any pool admin who allowlists the router (a natural action to let their users access the router) inadvertently opens the pool to all users, bypassing the allowlist entirely.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checks router, not user
```

**Step 1 — Router calls pool with itself as `msg.sender`:**

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly. [1](#0-0) 

**Step 2 — Pool passes `msg.sender` (router) as `sender` to `_beforeSwap`:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`. When the router is the caller, `msg.sender` is the router address. [2](#0-1) 

**Step 3 — `_beforeSwap` forwards the router address as `sender` to the extension:**

`ExtensionCalling._beforeSwap` encodes and dispatches `sender` (= router) to every configured extension. [3](#0-2) 

**Step 4 — `SwapAllowlistExtension` checks the router address, not the original user:**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool (correct), `sender` = router address (wrong — should be the original user). [4](#0-3) 

**The structural problem:** A pool admin who wants their allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** user who routes through it, regardless of whether that user is individually allowlisted. The extension has no way to recover the original user's address from the `sender` argument it receives.

---

### Impact Explanation

Any user — including those explicitly excluded from the allowlist — can trade on a restricted curated pool by routing through `MetricOmmSimpleRouter`. The allowlist, which is the sole access-control mechanism for swap gating on such pools, is rendered ineffective. Disallowed users can execute swaps, drain liquidity at oracle prices, and extract value from pools intended to be private or KYC-gated.

---

### Likelihood Explanation

The scenario requires the pool admin to have allowlisted the router address. This is the natural and expected action for any pool that wants to support both direct and router-mediated swaps for its allowlisted users. The router is a first-party, publicly deployed periphery contract, so allowlisting it is a routine operational step. The bypass is then available to any unprivileged user with no special setup.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` must gate on the **original user**, not the immediate pool caller. Two complementary approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.
2. **Check `recipient` instead of `sender`:** For single-hop swaps the recipient is often the user, but this breaks for multi-hop paths.
3. **Preferred — dedicated router forwarding:** The router should expose the original user via a standardized field (e.g., a `swapper` field in `extensionData`) and the extension should verify that the pool's `msg.sender` is a trusted router before trusting the forwarded identity.

---

### Proof of Concept

```solidity
// Pool P has SwapAllowlistExtension configured.
// Pool admin allowlists the router so that allowlisted users can use it:
swapAllowlist.setAllowedToSwap(P, address(router), true);

// Alice is NOT individually allowlisted:
// allowedSwapper[P][alice] == false

// Alice bypasses the allowlist by routing through the router:
router.exactInputSingle(ExactInputSingleParams({
    pool: P,
    recipient: alice,
    zeroForOne: true,
    amountIn: 1e18,
    ...
}));
// Pool calls _beforeSwap(msg.sender=router, ...)
// Extension checks allowedSwapper[P][router] == true  → passes
// Alice's swap executes on the restricted pool.
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
