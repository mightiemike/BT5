### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing complete allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` as the pool's `msg.sender`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating user. The allowlist check therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, making it impossible to simultaneously allow allowlisted users to trade through the router and block non-allowlisted users from doing the same.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` parameter to every configured extension. `SwapAllowlistExtension.beforeSwap` then uses it as the identity to gate: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap(...)` directly: [3](#0-2) 

At that point `msg.sender` to the pool is the router contract, so the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

The pool admin faces an irresolvable dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Every allowlisted user is also blocked from using the router |
| **Allowlist the router** | Every non-allowlisted user can bypass the gate by routing through the router |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (KYC'd users, institutional market-makers, or whitelisted addresses) loses that protection entirely for router-mediated swaps. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` against the pool and the extension will check the router address — which is either allowlisted (bypass succeeds) or not (all router users are blocked, including legitimate ones). The allowlist invariant — "only approved addresses may swap" — is broken for the standard periphery entry point.

**Severity: High** — direct policy bypass on curated pools; the router is the primary public entry point for swaps.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery contract for swaps. Any user who discovers the allowlist can trivially route through it. No privileged access, special tokens, or multi-step setup is required — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must check the originating user, not the intermediary. Two viable approaches:

1. **Pass the real user via `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Require direct pool interaction for allowlisted pools**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at pool creation (e.g., a flag that prevents the router from calling the pool). This is a breaking UX constraint.

The cleanest fix is approach (1): the router always appends the originating `msg.sender` to `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and gates on that address rather than the raw `sender` parameter.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured. Only `alice` is allowlisted: `allowedSwapper[pool][alice] = true`.
2. Pool admin also calls `setAllowedToSwap(pool, router, true)` so that `alice` can use the router (otherwise `alice`'s router swaps would also revert).
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. The pool calls `_beforeSwap(router, ...)`.
6. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. `bob`'s swap executes on the supposedly curated pool, bypassing the allowlist entirely. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
