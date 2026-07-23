### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual end-user swapper, making the allowlist ineffective for router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates pool swaps by checking the `sender` argument passed by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. This is a direct wrong-actor binding: the guard checks the intermediary, not the economically relevant actor.

---

### Finding Description

**Call chain:**

```
user → MetricOmmSimpleRouter.exactInputSingle(...)
         → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
              → MetricOmmPool._beforeSwap(msg.sender, ...)    // sender = router
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]         // ← wrong actor checked
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` encodes this `sender` and dispatches it to the configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When the router is the caller, `sender = address(router)`. The actual end-user who initiated the transaction is never visible to the extension.

The router calls `pool.swap()` directly with no mechanism to forward the original `msg.sender`: [4](#0-3) 

---

### Impact Explanation

Two mutually exclusive failure modes arise for any pool that uses `SwapAllowlistExtension` with the router:

**Mode A — Allowlisted users are blocked from the router (broken core functionality):**
The pool admin allowlists specific users (`alice`, `bob`). Those users call `router.exactInputSingle(...)`. The extension sees `sender = router`, which is not allowlisted → `NotAllowedToSwap` revert. Allowlisted users must bypass the router and call `pool.swap()` directly, making the primary periphery interface unusable for curated pools.

**Mode B — Allowlist bypass by any user (policy bypass):**
To fix Mode A, the pool admin allowlists the router address. Now `allowedSwapper[pool][router] = true`. Any unprivileged user — including those the admin explicitly excluded — can call `router.exactInputSingle(...)` and the extension passes, because it only checks the router address. The entire allowlist policy is nullified for router-mediated swaps.

Both modes are reachable without any malicious setup. Mode A requires only that the pool uses `SwapAllowlistExtension` with the router (the standard periphery path). Mode B requires only that the admin takes the natural corrective action of allowlisting the router.

---

### Likelihood Explanation

Any pool that:
1. Configures `SwapAllowlistExtension` as a `beforeSwap` hook, and
2. Expects users to interact via `MetricOmmSimpleRouter`

is affected. The router is the primary user-facing swap interface. The `SwapAllowlistExtension` is a first-class supported extension. The combination is the expected production configuration for curated pools.

---

### Recommendation

The extension must check the actual end-user, not the intermediary. Two approaches:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and checks this value. This requires the extension to trust the router, which requires a separate router-identity check.

2. **Check `sender` only when called directly; require router to pass user identity:** Define a convention where the router always encodes the real user in `extensionData`, and the extension reads from there when `sender` is a known router. This is the cleanest separation.

3. **Gate by `sender` and require direct pool calls for curated pools:** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the extension configuration level.

---

### Proof of Concept

```solidity
// Pool admin sets up curated pool
extension.setAllowedToSwap(pool, alice, true);   // alice is allowlisted
// bob is NOT allowlisted

// alice tries to swap through the router (normal user flow)
vm.prank(alice);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    ...
}));
// REVERTS: NotAllowedToSwap — extension sees sender=router, not alice

// Admin "fixes" by allowlisting the router
extension.setAllowedToSwap(pool, address(router), true);

// Now bob (not allowlisted) bypasses the allowlist
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    ...
}));
// SUCCEEDS: extension sees sender=router, which is allowlisted
// bob has bypassed the curated pool's allowlist
```

The root cause is that `SwapAllowlistExtension.beforeSwap` receives `sender = router` from the pool, while the allowlist is keyed by individual user addresses. The guard checks the wrong actor — the intermediary router — instead of the economically relevant actor — the end user — exactly mirroring the Rubicon owner/recipient confusion where the wrong address was used in a critical validation. [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
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
