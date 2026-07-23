### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router's address — not the original end-user. If the pool admin allowlists the router (the natural configuration to support router-mediated swaps for allowlisted users), every non-allowlisted user can bypass the curation gate by routing through the public router.

---

### Finding Description

**Call path:**

```
userB (not allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(pool, ...)
      → pool.swap(recipient, ..., extensionData)   // msg.sender = router
          → ExtensionCalling._beforeSwap(sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  → allowedSwapper[pool][router] == true  ← passes
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← router address, not the end-user
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

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...))   // sender = router
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool (correct for pool-identity), and `sender` = router address. The check resolves to `allowedSwapper[pool][router]`.

**The invariant break:** A pool admin who wants allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` is `true` for every caller of the router — including users who were never individually allowlisted.

Direct swaps correctly check the end-user (`allowedSwapper[pool][userA]`), but router-mediated swaps check the router (`allowedSwapper[pool][router]`). The two paths enforce different identities for the same economic action.

---

### Impact Explanation

**Direct loss / policy bypass on curated pools.** The entire purpose of `SwapAllowlistExtension` — restricting swaps to a vetted set of addresses (e.g., KYC'd counterparties, whitelisted market makers) — is defeated for any user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user executes a real swap against the pool's liquidity, receiving tokens out and paying tokens in, with no restriction. LP funds are exposed to counterparties the pool admin explicitly intended to exclude.

---

### Likelihood Explanation

**Medium-High.** The pool admin must allowlist the router for the allowlist to be usable in practice (otherwise allowlisted users cannot use the standard periphery). This is the expected operational configuration. Once the router is allowlisted, the bypass is trivially reachable by any user with no special privileges — a single call to `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` suffices.

---

### Recommendation

The extension must receive and check the original end-user's address, not the intermediary router's address. Two sound approaches:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Add a dedicated `originator` field to the swap hook signature:** Extend `IMetricOmmExtensions.beforeSwap` with an `originator` parameter that the pool populates from a trusted periphery-supplied value (e.g., a signed claim or a transient-storage slot set by the router before calling `swap`).

Until fixed, pool admins using `SwapAllowlistExtension` must not allowlist the router, which forces allowlisted users to call the pool directly and forfeits all router functionality (multi-hop, slippage checks, deadline, ETH wrapping).

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only userA is individually allowlisted.
// Pool admin also allowlists the router so userA can use it.
swapExt.setAllowedToSwap(pool, userA,  true);
swapExt.setAllowedToSwap(pool, router, true);  // ← required for router support

// userB is NOT allowlisted.
// Direct swap by userB → correctly reverts:
vm.prank(userB);
pool.swap(userB, false, 1000, type(uint128).max, "", "");
// → NotAllowedToSwap ✓

// Router swap by userB → incorrectly succeeds:
vm.prank(userB);
router.exactInputSingle(ExactInputSingleParams({
    pool:            pool,
    recipient:       userB,
    zeroForOne:      false,
    amountIn:        1000,
    amountOutMinimum: 0,
    priceLimitX64:   type(uint128).max,
    deadline:        block.timestamp + 1,
    extensionData:   ""
}));
// → swap executes; userB receives tokens from the curated pool ✗
```

The root cause is that `allowedSwapper[pool][router] == true` satisfies the check at [1](#0-0)  regardless of who called the router, because the pool passes `msg.sender` (the router) as `sender` to the hook at [2](#0-1) , which `ExtensionCalling._beforeSwap` forwards verbatim at [3](#0-2) . The router never records the original caller's identity in a form the extension can verify, as confirmed by the router's direct pool call at [4](#0-3) .

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
