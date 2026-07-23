### Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Original Swapper — Any User Bypasses Per-User Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates pool swaps by checking the `sender` argument passed by the pool, which equals `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the original EOA. If the router is allowlisted (a natural operational step so that allowlisted users can use the router), every user — including explicitly blocked ones — can bypass the per-user allowlist by routing through the public router contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the identity check:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct pool-identity binding). `sender` is the first argument forwarded by the pool:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) is used, the router is the direct caller of `pool.swap()`:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // extensionData forwarded verbatim; original user NOT embedded
    );
```

So `sender` in the extension equals `address(router)`, not the original EOA. The extension has no mechanism to recover the original user: `extensionData` is forwarded verbatim from the caller but `SwapAllowlistExtension` never decodes it.

**Bypass path:**

1. Pool admin deploys pool with `SwapAllowlistExtension`, allowlists user A (trusted), and also allowlists `address(router)` so that user A can swap via the router.
2. User B (explicitly not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
3. Router calls `pool.swap(...)` → pool calls `_beforeSwap(address(router), ...)`.
4. Extension evaluates `allowedSwapper[pool][address(router)]` → `true` → swap proceeds.
5. User B's swap executes at live oracle prices against pool liquidity, with no allowlist enforcement.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` because all four paths call `pool.swap()` with `msg.sender = router`.

---

### Impact Explanation

The `SwapAllowlistExtension` is the production guard for pools that restrict trading to specific counterparties (e.g., institutional LPs, whitelisted market makers, or pools that want to prevent public arbitrage during volatile oracle periods). Once the router is allowlisted — which is operationally necessary for any allowlisted user who wants to use the periphery — the guard is fully neutralised for all users. Unauthorized arbitrageurs can execute large swaps at live oracle prices, draining LP value in exactly the manner the allowlist was intended to prevent. This constitutes a direct loss of LP principal above Sherlock Medium thresholds.

---

### Likelihood Explanation

- The router is a public, permissionless contract.
- Any pool that wants allowlisted users to access the router **must** add `address(router)` to the allowlist; there is no other mechanism.
- Once the router is allowlisted, the bypass is trivially reachable by any EOA with no special privileges.
- The pool admin has no way to simultaneously allow specific users through the router and block others, because the extension cannot distinguish callers behind the same router address.

---

### Recommendation

The extension must gate on the **original user**, not the immediate pool caller. Two viable approaches:

1. **Router-forwarded identity in `extensionData`:** Standardize a convention where the router prepends `abi.encode(msg.sender)` to `extensionData` before forwarding to the pool. `SwapAllowlistExtension.beforeSwap` decodes the first word as the claimed original sender and verifies it. This requires a trusted router (already the case in this architecture).

2. **Transient-storage identity propagation:** The router writes the original `msg.sender` into a well-known transient slot before calling `pool.swap()`. The extension reads that slot directly. This avoids any `extensionData` encoding convention.

Either approach must be applied consistently across all `exact*` router entry points.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Admin allowlists trusted user AND the router (so trusted user can use periphery)
ext.setAllowedToSwap(pool, trustedUser, true);
ext.setAllowedToSwap(pool, address(router), true);  // ← necessary for periphery use

// Attack: blockedUser routes through the public router
vm.startPrank(blockedUser);
token0.approve(address(router), type(uint256).max);
// pool.swap() sees msg.sender = router → allowedSwapper[pool][router] = true → passes
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        recipient: blockedUser,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        tokenIn: token0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// blockedUser successfully swapped — allowlist completely bypassed
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-188)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint8 tradesLeftAfterThis = uint8(params.pools.length - 1);
    address pool = params.pools[tradesLeftAfterThis];
    bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, tradesLeftAfterThis);
    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _initCallbackContextforRecursiveOutput(
      pool, CALLBACK_MODE_EXACT_OUTPUT_ITERATE, tradesLeftAfterThis, msg.sender, params.tokens[0]
    );
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );

    int128 amountOut = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = _getExactOutputAmountIn();
    _clearExpectedCallbackPool();
  }
```
