### Title
`SwapAllowlistExtension` gates the router address instead of the originating user, allowing any unprivileged caller to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user enters through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router (the natural configuration for a curated pool that still wants to support the standard periphery), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Call chain that exposes the wrong actor:**

`MetricOmmPool.swap()` unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, ...)   // sender == pool's msg.sender
  )
);
```

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

The pool's `msg.sender` is now the **router address**. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The bypass:**

A pool admin who wants to support the standard periphery must allowlist the router. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[msg.sender][sender]` passes for every caller who routes through the router, regardless of whether that caller is individually allowlisted. Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)` and the guard silently passes.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all of them call `pool.swap()` from the router's address.

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole mechanism for restricting swap access on curated pools (e.g., KYC-gated, institutional, or compliance-restricted pools). A complete bypass means:

- Any unprivileged user can execute swaps on a pool that was explicitly configured to deny them.
- The pool's LP providers deposited under the assumption that only vetted counterparties would trade against their liquidity; that assumption is broken.
- Protocol fees and LP returns are generated from unauthorized trades, and the pool's compliance posture is silently violated.

This is a direct loss of the access-control invariant with fund-impacting consequences (unauthorized parties drain or trade against restricted LP capital).

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is a public, permissionless contract — any user can call it.
- Allowlisting the router is the only way for a curated pool to support the standard periphery UX; pool admins are expected to do this.
- No special knowledge, flash loans, or privileged access is required. A single `exactInputSingle` call suffices.
- The bypass is invisible on-chain: the transaction looks like a normal router-mediated swap.

---

### Recommendation

The extension must gate on the **originating user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the originating user through the pool.** The pool could accept an explicit `swapper` parameter (separate from `msg.sender`) that the router populates with `msg.sender` before calling the pool. The extension then checks that field. This requires a coordinated change to the pool interface and the router.

2. **Check `recipient` or require direct calls only.** As a simpler short-term fix, the extension can revert if `sender` is a known router address, forcing users to call the pool directly. This is fragile and does not scale.

3. **Preferred: gate on `msg.sender` inside the router, not inside the extension.** The router should verify the originating user is allowlisted before forwarding the call, using `ISwapAllowlistExtension.isAllowedToSwap(pool, msg.sender)` as a pre-flight check. This keeps the pool interface unchanged and closes the bypass at the periphery entry point.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT allowlist attacker address.

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({
       pool:      <curated pool>,
       recipient: attacker,
       ...
     })

  2. Router calls pool.swap(attacker, ...) — pool's msg.sender = router.

  3. Pool calls _beforeSwap(router, ...).

  4. ExtensionCalling forwards sender=router to SwapAllowlistExtension.beforeSwap.

  5. Extension evaluates: allowedSwapper[pool][router] == true → passes.

  6. Swap executes. Attacker receives output tokens.
     NotAllowedToSwap was never triggered despite attacker not being allowlisted.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
