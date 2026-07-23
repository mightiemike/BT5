### Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the EOA. If the pool admin allowlists the router address (a natural action to enable router-mediated swaps for their curated users), every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Call chain for a direct swap (correct):**
```
EOA → pool.swap()
  pool: _beforeSwap(msg.sender=EOA, ...)
  extension.beforeSwap(sender=EOA, ...)
  checks: allowedSwapper[pool][EOA]  ← correct actor
```

**Call chain for a router-mediated swap (broken):**
```
EOA → MetricOmmSimpleRouter.exactInputSingle()
  router → pool.swap()
    pool: _beforeSwap(msg.sender=router, ...)
    extension.beforeSwap(sender=router, ...)
    checks: allowedSwapper[pool][router]  ← wrong actor
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first argument to the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same applies to `exactInput` (multi-hop), `exactOutputSingle`, `exactOutput`, and the recursive `_exactOutputIterateCallback` — all call `pool.swap()` with the router as `msg.sender`: [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants to support router-mediated swaps for their allowlisted users has only one option: allowlist the router address. Doing so sets `allowedSwapper[pool][router] = true`, which causes the extension to pass for **any** `sender` value when `msg.sender` (the pool) is the caller and the router is the `sender`. Every unprivileged EOA can then call `router.exactInputSingle()` and swap against the curated pool without restriction. LP funds in the curated pool are exposed to unauthorized traders, violating the pool's curation invariant and potentially causing direct loss of LP principal through adverse selection.

---

### Likelihood Explanation

The pool admin faces an impossible choice: either allowlisted users cannot use the router at all (breaking UX), or the admin allowlists the router and inadvertently opens the gate to all users. A pool admin who reads the extension's NatDoc ("Gates `swap` by swapper address, per pool") has no reason to suspect that allowlisting the router grants universal access. The likelihood of a pool admin making this mistake is medium-to-high for any curated pool that intends to support periphery routing.

---

### Recommendation

The extension must resolve the original EOA rather than the immediate pool caller. Two options:

1. **Pass the original user through `extensionData`**: Require the router to encode the originating EOA in `extensionData` and have the extension decode and check that address. This requires a protocol-level convention.

2. **Check `sender` against the allowlist only when `sender` is not a known router; otherwise check the payer stored in transient context**: The router already stores the payer in transient storage (`T_SLOT_PAY_PAYER`). The extension could read that slot when `sender` is a recognized router.

3. **Simplest fix**: Remove router support from the allowlist model and require allowlisted users to call the pool directly, documenting this constraint explicitly.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `trustedUser` is allowlisted.
// Pool admin allowlists the router so trustedUser can use it:
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not allowlisted) routes through the router:
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    recipient: attacker,
    amountIn: 1000,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// ✓ Passes: extension checks allowedSwapper[pool][router] = true
// Attacker successfully swaps against the curated pool.
```

The `beforeSwap` hook receives `sender = address(router)`, which is allowlisted, so the guard passes regardless of who the originating EOA is. [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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
