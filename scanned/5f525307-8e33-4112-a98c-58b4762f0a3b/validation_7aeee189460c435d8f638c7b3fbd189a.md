### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Enabling Complete Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. A pool admin who allowlists the router to support router-mediated swaps inadvertently opens the allowlist to every user on the network.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
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

`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` argument to every configured extension.

`SwapAllowlistExtension.beforeSwap` then gates on that value:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct key namespace); `sender` is the direct caller of `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The router is `msg.sender` of that call, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same pattern applies to `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` argument (the economic actor), not `sender` (the intermediary payer). The swap extension has no equivalent "owner" concept and therefore gates the wrong identity.

---

### Impact Explanation

A pool admin who wants to support router-mediated swaps on a curated pool must allowlist the router address. Once `allowedSwapper[pool][router] = true`, every user on the network can call `MetricOmmSimpleRouter` and reach the pool, regardless of whether their own address is on the allowlist. The allowlist is completely defeated for all router-mediated paths.

Curated pools are designed to restrict counterparties (e.g., to prevent toxic flow, enforce KYC, or limit trading to specific market makers). Bypassing the allowlist exposes LP capital to unrestricted adverse-selection flow, directly threatening LP principal.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the protocol's official, publicly documented swap router. Any pool admin who wants users to be able to swap conveniently will allowlist the router. The bypass requires no special knowledge, no privileged access, and no non-standard tokens — any user can call `exactInputSingle` on the router against a curated pool.

---

### Recommendation

The extension must gate the original user, not the intermediary. Two viable approaches:

1. **Pass original sender through `extensionData`**: The router encodes `msg.sender` (the original user) into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.

2. **Add an `originalSender` field to the pool's swap interface**: The pool accepts an explicit `originalSender` parameter (similar to how `addLiquidity` separates `sender` from `owner`) and passes it to extensions. The router sets this to `msg.sender`.

The `DepositAllowlistExtension` pattern — checking `owner` rather than `sender` — is the correct model. The swap path needs an equivalent "original swapper" identity that survives router intermediation.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** call `setAllowedToSwap(pool, alice, true)`.
4. Alice (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → passes.
7. Alice's swap executes against the curated pool despite never being allowlisted.

The invariant "only allowlisted addresses may swap on a curated pool" is broken for every user who routes through the official router. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
