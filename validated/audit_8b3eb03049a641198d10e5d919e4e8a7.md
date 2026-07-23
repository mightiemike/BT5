### Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the originating user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the originating EOA. This creates an irreconcilable identity mismatch: to support router-mediated swaps for any allowlisted user, the pool admin must allowlist the router address itself — which then grants every user on-chain access to the curated pool, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

Here `msg.sender` = pool, `sender` = router. The check becomes `allowedSwapper[pool][router]`.

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) is used, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...,
    params.extensionData
);
```

The pool never sees the originating EOA — only the router address.

**Contrast with `DepositAllowlistExtension`**, which correctly checks the `owner` parameter (the economic beneficiary of the position), not `sender` (the immediate caller):

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address /*sender*/, address owner, ...) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The swap extension has no equivalent "original user" parameter available to it, because `pool.swap()` does not carry the originating EOA through to the extension.

---

### Impact Explanation

**Two fund-impacting outcomes arise from this mismatch:**

1. **Allowlist bypass (High):** A pool admin who wants allowlisted users to be able to swap via the router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, every user — including those explicitly not on the allowlist — can call `MetricOmmSimpleRouter.exactInputSingle` and trade on the curated pool. The allowlist is completely nullified for router-mediated paths.

2. **Broken withdraw/swap flow for allowlisted users (Medium):** If the pool admin does *not* allowlist the router, allowlisted EOAs cannot use the router at all. Their only path is a direct `pool.swap()` call, which requires them to implement `IMetricOmmSwapCallback`. This makes the router — the primary supported periphery — unusable for the very users the pool was designed to serve.

Both outcomes are contest-relevant: the first is a direct policy bypass enabling unauthorized trading on a curated pool; the second is broken core swap functionality for legitimate LP participants.

---

### Likelihood Explanation

**Medium.** The bypass requires the pool admin to allowlist the router, which is a natural and expected operational step for any curated pool that intends to support the standard periphery. The `MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who allowlists the router to enable normal usage unknowingly opens the pool to all users. No attacker privilege is required beyond calling the public router.

---

### Recommendation

Pass the originating user identity through the swap path so the extension can gate on the correct actor. Two approaches:

1. **Preferred — carry `payer`/`originator` in `extensionData`:** The router encodes `msg.sender` (the originating EOA) into `extensionData` before calling `pool.swap()`. The `SwapAllowlistExtension` decodes and checks this value. This requires no core changes.

2. **Alternative — add an `originator` field to `beforeSwap`:** Extend `IMetricOmmExtensions.beforeSwap` with an explicit `originator` parameter that the pool populates from a trusted source (e.g., a transient-storage slot set by the router before calling `swap`). This mirrors how `DepositAllowlistExtension` uses `owner` rather than `sender`.

Either way, the extension must check the actor who bears the economic consequence of the swap, not the intermediate contract that relays the call.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (necessary for any allowlisted user to use the router)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not on allowlist) calls:
      MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) — msg.sender = router
  - Pool calls _beforeSwap(router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes successfully for the non-allowlisted attacker

Result:
  - attacker trades on a curated pool they were explicitly excluded from
  - SwapAllowlistExtension provides zero protection for router-mediated swaps
```

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
