### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual End User, Allowing Any User to Bypass Per-User Swap Restrictions on Curated Pools — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` inside `MetricOmmPool.swap`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` in the pool is the router contract, not the actual end user. If the pool admin allowlists the router to support router-mediated swaps for approved users, any unprivileged user can bypass the per-user allowlist by calling the router.

---

### Finding Description

**Root cause — wrong actor bound in the hook:**

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` = pool): [3](#0-2) 

**How the router breaks the invariant:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The router is `msg.sender` inside the pool, so `sender` delivered to the extension is the router address, not the end user: [4](#0-3) 

The router stores the actual caller (`msg.sender`) only in transient storage for the payment callback — it is never forwarded to the pool or to any extension: [5](#0-4) 

**Contrast with the deposit allowlist (which is correct):**

`DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` entirely and checks `owner`, which is the explicit LP-position owner passed by the caller — correctly identifying the economically relevant actor regardless of who the intermediary is: [6](#0-5) 

The swap allowlist has no equivalent "actual user" parameter; it only has `sender` (the immediate caller of `pool.swap`).

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router (a reasonable step to let approved users trade through the standard periphery) inadvertently opens the pool to **all** users. Any non-allowlisted address can call `router.exactInputSingle` or `router.exactInput` targeting the restricted pool and the extension will pass because it sees `sender = router`, which is allowlisted. The curation invariant — "only approved addresses may trade" — is silently broken. Trades that should be blocked execute at live oracle prices, directly impacting LP principal and pool fee accounting.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router. This is a natural and expected configuration: without it, no user can reach the pool through the standard periphery at all. An admin who wants to support router-mediated swaps for approved users has no other option under the current design, so the misconfiguration is likely in any production curated pool that also supports the router.

---

### Recommendation

Pass the actual end user's address through the swap path so the extension can gate the economically relevant actor. Two options:

1. **Preferred**: Add an explicit `payer` or `originator` parameter to `pool.swap` (analogous to `owner` in `addLiquidity`) that the router populates with `msg.sender` before calling the pool. The extension then checks this field instead of `sender`.
2. **Alternative**: Have the router write the actual caller into a well-known transient storage slot that the extension reads directly, similar to how the callback context is stored today.

Also enforce in documentation and pool-admin guidelines that allowlisting the router is equivalent to `allowAllSwappers = true` under the current design.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension as extension1
  admin: allowedSwapper[pool][user1]  = true   // intended allowlisted trader
  admin: allowedSwapper[pool][router] = true   // to support router-mediated swaps

Attack (user2, not allowlisted):
  user2 calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)   // msg.sender in pool = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap:
        allowedSwapper[pool][router] == true   // PASSES
  user2 swap executes at live oracle price on the restricted pool
```

`user2` bypasses the per-user allowlist entirely. The pool admin's intent — "only `user1` may trade" — is violated by any caller who routes through the router.

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
