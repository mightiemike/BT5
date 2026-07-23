### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. If the pool admin allowlists the router to permit router-mediated swaps, every user on the network can bypass the allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` captures `msg.sender` and forwards it as the `sender` argument to `_beforeSwap`, which relays it to every configured extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` uses that `sender` argument — together with `msg.sender` (the pool) — to look up the allowlist: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, so the pool sees `msg.sender = router`: [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end-user]`. The same identity collapse occurs for `exactInput`, `exactOutputSingle`, and `exactOutput`.

There are two symmetric failure modes:

1. **Allowlist bypass**: The pool admin allowlists the router address so that router-mediated swaps work. Because the router is a public, permissionless contract, every user on the network can now call `exactInputSingle` and pass the extension check — the per-user restriction is completely defeated.

2. **Broken allowlist**: The pool admin allowlists individual user addresses. Those users cannot swap through the router (the standard interface) because the router's address fails the check. They must call `pool.swap` directly, which requires implementing `IMetricOmmSwapCallback` themselves.

---

### Impact Explanation

A pool protected by `SwapAllowlistExtension` is intended to restrict which addresses can trade against its liquidity. Under failure mode (1), any unprivileged user can execute swaps at oracle-anchored prices against a pool that was meant to be restricted, draining LP assets or extracting value at prices the LPs did not intend to offer to the general public. This is a direct loss of LP principal and constitutes a broken core pool invariant (the allowlist guard).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical, documented swap interface for the protocol. Any pool that deploys `SwapAllowlistExtension` and also wants to support router-mediated swaps (the normal user flow) must allowlist the router, which immediately opens the bypass to all users. The trigger requires no special privilege — any user calling `exactInputSingle` on the router is sufficient.

---

### Recommendation

The extension must receive the original end-user identity, not the immediate `msg.sender` of `pool.swap`. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should encode the original `msg.sender` into `extensionData` and the extension should decode it. This requires a convention between the router and the extension.

2. **Extension-side (preferred)**: Add a separate `allowedRouter` mapping. When `sender` is a known router, decode the real user from `extensionData` and check that address instead. Alternatively, gate by `recipient` (the address receiving tokens) rather than `sender`, since `recipient` is always the end-user-controlled address.

3. **Pool-level**: The pool could expose a separate `originalCaller` field in transient storage that periphery contracts set before calling `swap`, and extensions read from there. This is the cleanest architectural fix.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (necessary so that normal router swaps work)
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)
    (alice is not an intended swapper)

Attack:
  - alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Alice's swap executes successfully despite not being allowlisted

Result:
  - Alice trades against restricted LP liquidity at oracle-anchored prices
  - The per-user allowlist is completely bypassed
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at: [4](#0-3) 

where `sender` is the router address, not the end user, whenever the swap is routed through `MetricOmmSimpleRouter`: [5](#0-4)

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
