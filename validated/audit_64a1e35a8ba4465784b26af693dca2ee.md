### Title
`SwapAllowlistExtension` checks the router address instead of the actual user, allowing any user to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the actual user. If the pool admin allowlists the router to enable router-based swaps, every non-allowlisted user can bypass the per-user restriction by calling through the router.

---

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to the extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` — so `sender` seen by the extension is the **router address**, not the actual user: [3](#0-2) 

This creates an irreconcilable dilemma for any pool admin who wants to run a curated pool with router support:

- If the **router is not allowlisted**: allowlisted users cannot use the router at all (their swap reverts because `sender = router` is not in the allowlist).
- If the **router is allowlisted**: every non-allowlisted user can bypass the per-user restriction by routing through the router.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the economic actor for deposits), not `sender` (the operator/adder contract): [4](#0-3) 

The pool's `addLiquidity` separates `sender` (the payer/operator) from `owner` (the position owner), so the deposit allowlist can correctly gate the economic actor. The swap path has no equivalent separation — `sender` is the only identity available, and it collapses to the router address on any router-mediated swap.

---

### Impact Explanation

A pool admin who configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) and also allowlists the router to let those users trade conveniently will inadvertently open the pool to all users. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and the extension will see `sender = router`, which passes the check. The curated pool's access control is silently nullified, allowing unauthorized parties to trade against LP funds under terms the pool admin did not intend.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who deploys a curated pool and wants their allowlisted users to be able to use the standard router will encounter this. The allowlisting of the router is a natural operational step, not an exotic configuration. The bypass requires no special privileges — any EOA can call the router.

---

### Recommendation

The `beforeSwap` hook should gate on an identity that survives router intermediation. Two options:

1. **Pass the economic actor explicitly**: Extend the `extensionData` convention so the router encodes the originating user, and have the extension decode and verify it. This requires trust that the router is the only allowed `sender` when this field is present.

2. **Gate on `sender` only when `sender` is not a known router**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, require the `recipient` (or a user field in `extensionData`) to be allowlisted instead.

3. **Align with the deposit pattern**: Introduce a `swapper` field in the swap path (analogous to `owner` in `addLiquidity`) that the pool passes through as the economic actor, separate from the operator/router `sender`. The extension would then check this field.

The simplest safe fix for the current design is to document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and must only be used on pools where all swappers call `pool.swap()` directly — and enforce this by reverting if `sender` is a known router address.

---

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — alice is the only allowed swapper.
3. Pool admin calls setAllowedToSwap(pool, router, true) — router is allowlisted so alice can use it.
4. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(recipient=bob, ...).
6. Pool calls extension.beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes on the curated pool despite not being allowlisted.
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
