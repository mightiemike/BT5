### Title
`SwapAllowlistExtension` checks the router address instead of the actual swapper, enabling full allowlist bypass for any user via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps, every user — including non-allowlisted ones — can bypass the per-user allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), NOT the end user
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value verbatim to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the direct caller of `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)`:

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

So `msg.sender` inside `pool.swap()` is the **router**, not the end user. The allowlist check becomes `allowedSwapper[pool][router]` — it checks whether the router is allowlisted, not whether the actual user is allowlisted.

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` (the position owner explicitly passed through the call chain), not `sender` (the direct caller):

```solidity
// DepositAllowlistExtension.sol line 38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The swap extension has no equivalent "owner" parameter — it relies on `sender`, which is the wrong actor when routing through the router.

---

### Impact Explanation

**Allowlist bypass (High):** A pool admin who wants to allow router-mediated swaps must allowlist the router address (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** user who routes through the router, regardless of whether that user is individually allowlisted. The per-user curation is completely defeated. Any unprivileged user can swap on a pool that was intended to be restricted to a specific set of counterparties.

**Broken core swap functionality (High):** If the admin does not allowlist the router, then individually allowlisted users who attempt to swap through the router are blocked (`allowedSwapper[pool][router] == false`), even though they are explicitly permitted. The primary user-facing swap path becomes unusable for all allowlisted users.

---

### Likelihood Explanation

The router is the standard, documented entry point for swaps. Pool admins configuring a curated pool with `SwapAllowlistExtension` will naturally need to decide whether to allowlist the router. Either choice produces a broken outcome: allowlisting the router opens the pool to everyone; not allowlisting it blocks all router users. The likelihood that at least one production pool reaches this broken state is high.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **actual economic actor**, not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.
2. **Mirror the deposit pattern**: Introduce an explicit `swapper` parameter (analogous to `owner` in `addLiquidity`) that the pool accepts and forwards to extensions, so the router can pass the real user address.

Until fixed, `SwapAllowlistExtension` should not be used on pools that are expected to receive router-mediated swaps.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is intended to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to allow router-mediated swaps.
4. Attacker (Eve, not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Eve successfully swaps on a pool she was never authorized to access. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
