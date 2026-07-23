### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass Swap Allowlist on Curated Pools via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` becomes the router's address rather than the actual user's address. If the router is allowlisted on a curated pool, every user—including those not individually allowlisted—can execute swaps by routing through the router, completely defeating the per-user curation policy.

---

### Finding Description

**Step 1 — Extension checks `sender`, which is the direct caller of `pool.swap()`.**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the extension's caller), so the check resolves to `allowedSwapper[pool][sender]`.

**Step 2 — The pool passes its own `msg.sender` as `sender` to the extension.**

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

```solidity
// metric-core/contracts/ExtensionCalling.sol:149-165
function _beforeSwap(address sender, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
```

**Step 3 — `MetricOmmSimpleRouter` calls `pool.swap()` directly, so `sender` = router.**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
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

The actual user (`msg.sender` of `exactInputSingle`) is stored only in transient storage for the payment callback; it is never forwarded to the pool or the extension. The extension therefore sees `sender = router`, not the actual user.

**Step 4 — The bypass.**

A pool admin who wants to allow their allowlisted users to use the router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the extension passes for every call that arrives through the router, regardless of who the actual user is. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle / exactInput / exactOutputSingle / exactOutput` and the extension will approve the swap because it only sees the router.

The same identity mismatch applies to multi-hop `exactInput` and `exactOutput` paths, where intermediate hops also originate from the router.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps, drain liquidity at oracle-derived prices, and extract value from LP positions that were intended to be accessible only to vetted counterparties. This is a direct bypass of a configured security control with fund-impacting consequences for LPs on curated pools.

---

### Likelihood Explanation

The likelihood is **medium**. The bypass requires the pool admin to allowlist the router address. A pool admin who wants their allowlisted users to be able to use the standard periphery router will naturally add the router to the allowlist, not realizing that doing so opens the pool to all router users. There is no mechanism in the extension or the router to forward the actual user's identity, so there is no correct way to configure the allowlist for router-mediated swaps. The mistake is structurally encouraged by the design.

---

### Recommendation

The `SwapAllowlistExtension` must check the identity of the economic actor, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Router-forwarded identity via `extensionData`:** Modify `MetricOmmSimpleRouter` to encode the actual user's address into `extensionData` and modify `SwapAllowlistExtension` to decode and check it when `sender` is a known router. This requires the extension to maintain a registry of trusted routers.

2. **Check `sender` and fall back to decoded `extensionData`:** The extension checks `allowedSwapper[pool][sender]` first; if `sender` is a registered router, it decodes the real user from `extensionData` and checks `allowedSwapper[pool][realUser]`. This preserves backward compatibility for direct pool calls.

Either approach must ensure the identity field in `extensionData` cannot be spoofed by an arbitrary caller.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwapOrder`.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so that `userA` can use it.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `_beforeSwap(router, ...)`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `userB` successfully swaps on a pool they are not allowlisted for, receiving output tokens at oracle-derived prices. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-165)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
