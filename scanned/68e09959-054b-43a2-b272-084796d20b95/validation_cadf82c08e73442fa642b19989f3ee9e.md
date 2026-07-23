### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap()` receives `msg.sender = router`, so the extension checks the router's address rather than the actual user's address. If the router is allowlisted (the only way to permit router-mediated swaps on a curated pool), every user in the world can bypass the allowlist by routing through the router.

---

### Finding Description

**Root cause — wrong actor bound to the guard:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// ExtensionCalling.sol
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks `sender` (the router) against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`), the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The pool sees `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Two broken invariants:**

1. **Allowlist bypass (security):** A pool admin who wants to allow router-mediated swaps must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call, so every user — including those the admin explicitly excluded — can swap freely by routing through `MetricOmmSimpleRouter`.

2. **Broken core functionality (usability):** A pool admin who allowlists specific users but not the router causes those users to be unable to use the router at all. Their address is allowlisted, but the extension sees the router and reverts.

**Analog to the external bug:** In the Deriverse report, `margin_call = true` is set globally for all orders whenever any liquidation candidate exists, disabling referral rewards for all users. Here, the allowlist check is applied to the wrong actor (the router) for all router-mediated swaps, disabling the intended per-user access control for all such swaps.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker does not need any special privilege — `MetricOmmSimpleRouter` is a public, permissionless contract. The pool admin's allowlist configuration is silently rendered ineffective. Any user can execute swaps on a pool that was intended to be private, draining LP liquidity at oracle-quoted prices.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented in the periphery. Any pool that deploys `SwapAllowlistExtension` and also expects users to interact via the router is immediately vulnerable. The bypass requires no special setup: any address calls `exactInputSingle` on the router pointing at the curated pool.

---

### Recommendation

The extension must check the economically relevant actor — the end user — not the intermediary. Two approaches:

1. **Pass the original user through the router:** Have `MetricOmmSimpleRouter` forward the original `msg.sender` as an explicit `sender` field in `extensionData`, and have the extension decode and check that value. This requires a coordinated change between the router and the extension.

2. **Check `recipient` or decode from `extensionData`:** The pool admin can require that the actual user's address be embedded in `extensionData` and verified inside the extension. The router already forwards `extensionData` unchanged.

3. **Require direct pool calls for allowlisted pools:** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level.

The cleanest fix is for the pool to pass the original caller's identity through a dedicated field rather than relying on `msg.sender`, which is always the immediate caller (the router).

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps (or equivalently, `setAllowAllSwappers(pool, false)` with only the router allowlisted).
3. Attacker (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle({pool: curated_pool, ...})`.
4. Router calls `pool.swap(...)` with `msg.sender = router`.
5. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` evaluates `allowedSwapper[pool][router] == true` → passes.
6. Attacker's swap executes against the curated pool's LP liquidity despite never being allowlisted.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
