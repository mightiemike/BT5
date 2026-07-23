### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument (which is `msg.sender` of the pool's `swap` call) against the per-pool allowlist. When users swap through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end user. If the pool admin allowlists the router address to support periphery-mediated swaps, every user — including non-allowlisted ones — can bypass the guard by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` always passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the caller of the extension) and `sender` is whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The router never forwards the original user's identity to the pool. The pool's `swap` signature has no `sender` parameter — it always derives sender from `msg.sender`. Therefore the extension sees `sender = router address`, not the end user.

A pool admin who wants to support router-mediated swaps must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[msg.sender][sender]` passes for **every** user who routes through the router, regardless of whether that user is individually allowlisted.

Additionally, `SwapAllowlistExtension.beforeSwap` drops the `onlyPool` modifier that the base class declares, meaning the function is callable by any address. While this alone does not create a bypass (the pool still invokes the extension during live swaps), it removes the defense-in-depth layer that would prevent a rogue caller from probing or misusing the extension entry point.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to KYC'd or whitelisted counterparties loses that guarantee the moment the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and execute swaps on the restricted pool. The allowlist policy is completely nullified for router-mediated paths, which is the primary user-facing entry point for the protocol.

**Severity: Medium** — direct policy bypass on curated pools; the allowlist invariant is broken for all router-mediated swaps.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical periphery swap entry point. Any pool admin who wants end users to be able to swap through the router (the expected UX) must allowlist the router. This is a natural, expected configuration step. The bypass is therefore reachable on any allowlisted pool that also supports router access, which is the common case.

---

### Recommendation

The extension must gate the **economic actor** (the end user), not the intermediary (the router). Two viable approaches:

1. **Pass the originating user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. The pool already forwards `extensionData` unchanged to every extension hook.

2. **Check both router and originating user**: Require the router to be a trusted forwarder that appends the original user address, and have the extension verify the appended address against the allowlist when `sender` is a known router.

The base class `onlyPool` modifier should also be restored in `SwapAllowlistExtension.beforeSwap` and `DepositAllowlistExtension.beforeAddLiquidity` overrides to prevent direct external calls to the extension.

---

### Proof of Concept

**Setup**:
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin allowlists Alice (a KYC'd user): `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router to support periphery access: `setAllowedToSwap(pool, router, true)`.

**Attack**:
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — `msg.sender = router`.
6. Pool calls `_beforeSwap(msg.sender=router, ...)` → `ExtensionCalling._callExtensionsInOrder` → `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes successfully on the restricted pool, bypassing the allowlist entirely.

**Direct pool call (control)**:
- Bob calls `pool.swap(...)` directly → `sender = bob` → `allowedSwapper[pool][bob]` = false → reverts with `NotAllowedToSwap`. ✓

The bypass is exclusive to the router path, confirming the wrong-actor binding as the root cause. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
