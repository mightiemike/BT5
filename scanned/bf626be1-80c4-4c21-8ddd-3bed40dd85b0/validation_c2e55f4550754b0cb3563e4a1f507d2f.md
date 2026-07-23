### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the `sender` delivered to the extension is the **router's address**, not the actual end user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user — including those not individually allowlisted — can bypass the per-user gate by calling through the router.

---

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap()` is the **router**, so `sender` delivered to the extension is the router address — not the actual end user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same identity mismatch applies to multi-hop `exactInput` (hops 1-N use `address(this)` = router as sender) and `exactOutput` (recursive callback swaps also originate from the router): [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses. To allow those allowlisted users to also use the router, the admin must add the router to the allowlist. Once the router is allowlisted, **any** address — including those explicitly not allowlisted — can bypass the per-user gate by calling through `MetricOmmSimpleRouter`. The allowlist policy is completely nullified for router-mediated flows. This is a broken core pool functionality: the curation mechanism the pool was configured to enforce does not apply to the supported periphery path.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any user aware that a pool uses a swap allowlist can trivially route through the router. No special privileges, flash loans, or unusual token behavior are required. The trigger is a normal public call.

---

### Recommendation

The extension must gate the **economically relevant actor** — the human or contract that initiated the trade — not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Pass the original initiator through the router**: have the router forward `msg.sender` as an additional field in `extensionData`, and have the extension decode and check that address. This requires a coordinated extension/router convention.
2. **Check `recipient` instead of `sender`**: for a swap allowlist the recipient is often the correct identity to gate; however this depends on pool design intent.
3. **Require direct pool calls for allowlisted pools**: document that pools using `SwapAllowlistExtension` must not allowlist the router, and allowlisted users must call `pool.swap()` directly. This is a usage restriction, not a code fix.

The cleanest fix is option 1: the router should encode `msg.sender` into `extensionData` and the extension should decode and verify it, so the actual user identity is always available to the guard regardless of the call path.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required so alice can use the router
4. bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) — msg.sender inside pool = router
6. Pool calls _beforeSwap(sender=router, ...)
7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes
8. bob's swap executes successfully despite not being individually allowlisted.
```

The allowlist check passes because `sender = router` is allowlisted, not because `bob` is. The per-user curation policy is fully bypassed. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
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
```
