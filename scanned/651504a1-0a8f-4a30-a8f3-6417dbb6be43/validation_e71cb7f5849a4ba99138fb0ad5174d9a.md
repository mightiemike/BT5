### Title
`SwapAllowlistExtension` checks router address instead of actual user — allowlist fully bypassed via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps using the `sender` parameter, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual end user. Any admin who allowlists the router to let their users swap inadvertently opens the pool to every address on-chain, completely defeating the per-user access control.

---

### Finding Description

The pool's `swap` function passes `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other swap entry point) calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

Two broken outcomes follow:

1. **Allowlisted users are silently blocked.** If the admin allowlists `alice` but not the router, Alice's router swap checks `allowedSwapper[pool][router]` → `false` → `NotAllowedToSwap`. Alice must implement `IMetricOmmSwapCallback` herself and call the pool directly.

2. **Complete allowlist bypass.** To unblock their users, the admin allowlists the router: `allowedSwapper[pool][router] = true`. Now `allowAllSwappers[pool]` is still `false`, but `allowedSwapper[pool][router]` is `true`, so the condition passes for **every** caller that routes through `MetricOmmSimpleRouter`, regardless of their individual allowlist status.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the position owner explicitly supplied by the caller), not `sender`, so it is not affected: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` for access control (e.g., a KYC-gated or institutional-only pool) can be fully bypassed by any unprivileged user calling `MetricOmmSimpleRouter`. If the pool offers subsidized or oracle-anchored pricing unavailable on open markets, unauthorized users can drain liquidity at below-market rates, causing direct loss of LP principal. The allowlist — the sole access-control mechanism for swaps — provides no protection once the router is allowlisted.

---

### Likelihood Explanation

The router is the primary user-facing interface documented and deployed alongside the protocol. Pool admins who configure `SwapAllowlistExtension` will almost certainly need to allowlist the router to make the pool usable for their intended audience. The bypass is therefore a near-certain consequence of normal operational setup, not an edge case.

---

### Recommendation

Replace the `sender` check with a check against the actual end user. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Mirror `DepositAllowlistExtension`**: Add a `recipient` parameter check (the second argument to `beforeSwap`) instead of `sender`, or require the pool to expose a separate "originator" field. The cleanest fix is for the pool to pass the original `tx.origin`-equivalent through a dedicated parameter rather than reusing `msg.sender`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension (beforeSwap order = extension 1)
  admin calls: swapExt.setAllowedToSwap(pool, alice, true)
  admin calls: swapExt.setAllowedToSwap(pool, router, true)  // needed so alice can use router

Attack:
  mallory (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: mallory, ...})

Execution trace:
  router.exactInputSingle()          msg.sender = mallory
    pool.swap(recipient=mallory, ...) msg.sender = router
      extension.beforeSwap(sender=router, ...)
        allowedSwapper[pool][router] == true  → PASSES
  mallory receives token output from restricted pool
```

Mallory, who was never allowlisted, successfully swaps because the extension sees `sender = router` (allowlisted) rather than `sender = mallory` (not allowlisted).

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
