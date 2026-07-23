### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of the pool call is the **router**, not the end user. The extension therefore checks whether the router is allowlisted, not whether the actual trader is allowlisted. A pool admin who adds the router to the allowlist (the natural step to support router-mediated swaps) inadvertently opens the pool to every user, completely defeating the curation mechanism.

---

### Finding Description

**Pool `swap()` passes `msg.sender` as `sender` to the extension:** [1](#0-0) 

```solidity
_beforeSwap(
  msg.sender,   // ← this is the router when called via MetricOmmSimpleRouter
  recipient,
  ...
  extensionData
);
```

**`ExtensionCalling._beforeSwap` forwards that value as the `sender` argument:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router:** [3](#0-2) 

```solidity
function beforeSwap(address sender, ...)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  ...
}
```

**`MetricOmmSimpleRouter` calls the pool directly; the pool sees the router as `msg.sender`:** [4](#0-3) 

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

The pool's `swap()` is called with `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

This creates two mutually exclusive broken states:

| Router in allowlist? | Effect |
|---|---|
| **No** | All router-mediated swaps blocked, even for individually allowlisted users — core functionality broken |
| **Yes** | Every user on the network can bypass the allowlist by routing through the router — curation completely defeated |

The same wrong-actor binding affects `exactInput`, `exactOutputSingle`, and `exactOutput` because all of them call `pool.swap()` from the router context. [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and adds the router to the allowlist (the only way to let allowlisted users trade via the router) simultaneously grants swap access to every address on the network. Any non-allowlisted user can call `exactInputSingle` or any other router entry point and the extension will approve the swap because it sees the router — which is allowlisted — as the swapper. This is a complete admin-boundary break: the pool admin's configured access control is bypassed by any unprivileged caller through a supported public periphery path.

---

### Likelihood Explanation

The scenario is highly likely in practice. Any pool operator who wants their allowlisted LPs or counterparties to use the standard router must add the router to the allowlist. The moment they do, the allowlist is open to all. The trigger requires no special permissions, no flash loans, and no multi-transaction setup — a single `exactInputSingle` call from any EOA suffices.

---

### Recommendation

Pass the original end-user address through the swap call so the extension can check it. The standard pattern is to include the originating caller in `callbackData` or a dedicated field, then forward it through the extension payload. Concretely:

1. In `MetricOmmSimpleRouter`, encode `msg.sender` (the real trader) into `extensionData` before calling `pool.swap()`.
2. In `SwapAllowlistExtension.beforeSwap`, decode and check that address instead of the raw `sender` argument.

Alternatively, the pool's `swap()` interface could accept an explicit `originator` parameter that the router populates with `msg.sender` and the extension checks.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool deployed with ext as beforeSwap hook

// Admin allowlists Alice (intended user) and the router (to support router swaps)
ext.setAllowedToSwap(pool, alice, true);
ext.setAllowedToSwap(pool, address(router), true);  // ← natural step

// Attack: Bob (not allowlisted) swaps via the router
// pool.swap() is called with msg.sender = router
// Extension checks: allowedSwapper[pool][router] == true → PASSES
// Bob's swap executes on the curated pool despite not being allowlisted
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    recipient: bob,
    amountIn: 1e18,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Bob successfully trades on a pool he was never meant to access
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37, which reads `sender` (the router) instead of the originating user: [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-147)
```text
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
