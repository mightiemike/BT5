### Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is documented as gating `swap` by **swapper address, per pool**. However, the `sender` it checks is the direct `msg.sender` of `pool.swap()`, not the end-user. When `MetricOmmSimpleRouter` is the caller, `sender` = router address. A pool admin who allowlists the router to enable router-based swaps inadvertently opens the pool to **every** user, defeating the curation invariant entirely.

---

### Finding Description

**Actor binding in the extension:** [1](#0-0) 

`beforeSwap` receives `sender` as its first argument and checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` = pool (the extension caller) and `sender` = whoever called `pool.swap()`.

**How the pool populates `sender`:** [2](#0-1) 

The pool passes `msg.sender` — its own direct caller — as `sender` to every extension hook.

**How the router calls the pool:** [3](#0-2) 

`exactInputSingle` calls `pool.swap(...)` directly. The pool's `msg.sender` is therefore the **router address**, not the end-user. The extension consequently evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable configuration trap:

| Pool admin action | Effect |
|---|---|
| Allowlist individual users | Router-based swaps blocked for everyone (router not allowlisted) |
| Allowlist the router address | Every user bypasses the allowlist through the router |

There is no configuration that enforces per-user gating when users go through the supported periphery router.

The same structural problem applies to multi-hop `exactInput`: [4](#0-3) 

For intermediate hops, `sender` = `address(this)` (the router itself), compounding the mismatch.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router address — a natural step to enable the supported periphery path — inadvertently grants swap access to **all** users. Any non-allowlisted address can call `router.exactInputSingle(pool, ...)` and the extension passes because `allowedSwapper[pool][router] = true`. The curation boundary is fully erased: the pool behaves as if `allowAllSwappers = true` for every router user. This breaks the core allowlist invariant ("a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it") and constitutes a broken core pool functionality with direct fund-impact potential if the pool's pricing is calibrated for a restricted participant set.

---

### Likelihood Explanation

Medium. The trigger is a semi-trusted pool admin allowlisting the router — a natural, non-malicious action taken to enable the protocol's own supported periphery. The admin has no reason to suspect that allowlisting the router grants access to all users rather than routing-capable users. The `SwapAllowlistExtension` contract carries no documentation warning of this actor-binding limitation.

---

### Recommendation

The extension must verify the **economic actor** (the end-user), not the intermediary. Two viable approaches:

1. **Router-side forwarding:** Have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it — with a pool-level flag indicating that the extension trusts the router to supply the real user.
2. **Pool-level actor field:** Add a dedicated `payer` or `originator` field to the hook signature so the pool can propagate the true initiator independently of `msg.sender`.

Until fixed, pool admins using `SwapAllowlistExtension` must not allowlist the router address and must instruct users to call `pool.swap()` directly, forgoing the supported periphery.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls:
       swapExtension.setAllowedToSwap(pool, router, true)
   (intending to allow router-based swaps for allowlisted users)
3. Non-allowlisted attacker calls:
       router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})
4. Router executes:
       IMetricOmmPoolActions(pool).swap(recipient, ...)
   → pool.msg.sender = router
5. Pool calls:
       _beforeSwap(router, recipient, ...)
6. Extension evaluates:
       allowedSwapper[pool][router] == true  → passes
7. Swap executes. Attacker trades in the curated pool without being individually allowlisted.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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
