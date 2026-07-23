Now I have all the code needed to trace the full call chain. Let me analyze the exact flow.

**Call chain for a router-mediated swap:**

1. User (EOA) → `MetricOmmSimpleRouter::exactInputSingle`
2. Router → `IMetricOmmPoolActions(pool).swap(...)` — here `msg.sender` to the pool is the **router address**
3. Pool `swap` → `_beforeSwap(msg.sender, ...)` — `sender` = **router address**
4. `ExtensionCalling::_beforeSwap` → calls extension with `sender` = **router address**
5. `SwapAllowlistExtension::beforeSwap(sender=router, ...)` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`

The hook never sees the original user's address.

---

### Title
Router-Mediated Swaps Pass Router Address as Swapper to `SwapAllowlistExtension`, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[pool][sender]`. The pool passes `msg.sender` as `sender`, which is the **router** when a swap is routed through `MetricOmmSimpleRouter`. The original user's address is never forwarded. A pool admin who allowlists the router (a natural action to enable router-based swaps for their allowlisted users) inadvertently opens the pool to **all** users, completely defeating the allowlist.

### Finding Description

In `MetricOmmPool::swap`, the pool passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards this as `sender` to the extension: [2](#0-1) 

`SwapAllowlistExtension::beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When the router calls the pool, `msg.sender` to the pool is the router contract. The router does not forward the original user's address to the pool's `swap` call: [4](#0-3) 

The router stores the original `msg.sender` only in transient storage for the payment callback — it is never passed into the pool's `swap` arguments. So the hook always sees the router's address as `sender`, not the original user.

This creates two failure modes:

**Mode A — Allowlist bypass (higher impact):** The pool admin allowlists the router address (a natural action to enable router-based swaps for their allowlisted users). Now `allowedSwapper[pool][router] = true`, so **any** user who calls the router can swap in the restricted pool, bypassing the allowlist entirely.

**Mode B — Allowlisted users locked out of router:** The pool admin allowlists specific EOAs but not the router. Those EOAs cannot use the router at all — their swaps revert with `NotAllowedToSwap` because the router's address is not allowlisted.

### Impact Explanation

The `SwapAllowlistExtension` is the primary access-control mechanism for restricting which addresses may swap in a pool. Mode A allows any unprivileged user to bypass this guard entirely by routing through `MetricOmmSimpleRouter`, breaking the core invariant that only allowlisted swappers may trade. This constitutes broken core pool functionality and, depending on the pool's design (e.g., a private pool for specific market makers), can result in unauthorized trades and direct fund loss through unwanted price impact or liquidity drain.

### Likelihood Explanation

The pool admin must allowlist the router for Mode A to trigger. This is a natural and expected action — any admin who wants their allowlisted users to be able to use the standard router will do exactly this. There is no warning in the code or interface that allowlisting the router opens the pool to all users. The `setAllowedToSwap` function treats the router address identically to any other address: [5](#0-4) 

### Recommendation

The extension should not rely solely on the `sender` argument passed from the pool. Options include:

1. **Pass the original user through `extensionData`:** The router encodes the original `msg.sender` into `extensionData`, and the extension decodes and verifies it (requires trust in the router, which can be checked against the factory).
2. **Check both `sender` and `recipient`:** Gate on the recipient address as a proxy for the intended beneficiary.
3. **Document the limitation clearly:** If the design intent is to gate the immediate pool caller only, document that the extension is incompatible with router-mediated swaps and that allowlisting the router bypasses per-user restrictions.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured in beforeSwap slot.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — only Alice is allowed.
3. Pool admin calls setAllowedToSwap(pool, router, true) — to let Alice use the router.
4. Bob (not allowlisted) calls MetricOmmSimpleRouter::exactInputSingle with the pool.
5. Router calls pool.swap(...) with msg.sender = router.
6. beforeSwap receives sender = router; allowedSwapper[pool][router] = true → passes.
7. Bob's swap executes successfully despite not being allowlisted.
```

The allowlist is fully bypassed. Any user routing through the router is treated as the router, which is allowlisted.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-20)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
