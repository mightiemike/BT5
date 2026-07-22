### Title
Swap Allowlist Guard Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument it receives from the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `swap()` is called by the router (`msg.sender = router`), so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router to support router-mediated swaps, every user — including non-allowlisted ones — can bypass the guard entirely.

---

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`, passing `msg.sender` (the direct caller of `swap`) as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

At this point `msg.sender` inside the pool is the **router address**, not the end-user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same mismatch applies to multi-hop `exactInput` and `exactOutput` paths: [5](#0-4) 

---

### Impact Explanation

A pool admin who wants to support router-mediated swaps for allowlisted users must add the router to the allowlist. Once the router is allowlisted, **any** user — regardless of their own allowlist status — can call `MetricOmmSimpleRouter.exactInputSingle()` and the extension will pass the check (because the router is allowed). The per-user allowlist is completely defeated.

Conversely, if the admin does **not** allowlist the router, allowlisted users are silently blocked from using the router even though they are individually permitted. This is the direct analog to the M-7 pattern: the guard evaluates the wrong identity at the wrong point in the call chain, producing either a false-pass (bypass) or a false-fail (blocked legitimate user).

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router — a natural and expected action for any pool that intends to support the standard periphery swap path. The `IMetricOmmPoolActions` NatSpec explicitly documents the operator pattern for `addLiquidity` and the same expectation carries to swaps. Any pool that enables router-mediated swaps for its allowlisted users is immediately vulnerable to the bypass by all other users. [6](#0-5) 

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **end-user identity**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the original user in `extensionData`**: The router encodes `msg.sender` (the end-user) into `extensionData` before calling `pool.swap()`. The extension decodes and checks that address. The pool admin must trust the router to supply honest data, so this should be combined with an allowlist of trusted routers.

2. **Check `sender` only for direct pool calls; require routers to pass user identity**: Define a convention where the extension reads the user address from `extensionData` when `sender` is a known router, and falls back to `sender` otherwise.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Admin allowlists `alice` (`allowedSwapper[pool][alice] = true`) and the router (`allowedSwapper[pool][router] = true`) to support router-mediated swaps for `alice`.
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle()` targeting the pool.
4. The router calls `pool.swap(recipient, ..., extensionData)` with `msg.sender = router`.
5. `_beforeSwap` passes `sender = router` to `SwapAllowlistExtension.beforeSwap()`.
6. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. `bob`'s swap executes successfully despite not being on the allowlist.

The guard checked `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][bob]`, allowing an unauthorized user to trade in a restricted pool. [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-148)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
```
