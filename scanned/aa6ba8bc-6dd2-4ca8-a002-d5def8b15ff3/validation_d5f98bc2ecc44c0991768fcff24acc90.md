### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. Any pool that allowlists the router to support router-based swaps therefore grants every user — including those not individually allowlisted — the ability to bypass the curated allowlist entirely.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
              → MetricOmmPool._beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards it verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()`, the pool's `msg.sender` is the router: [4](#0-3) 

The router does **not** forward the original `msg.sender` (the actual user) to the pool. The pool therefore sees `sender = router_address`. The allowlist check becomes `allowedSwapper[pool][router]` — a check on the intermediary, not the economic actor.

For any user to swap through the router on an allowlisted pool, the pool admin must add the router to the allowlist. Once the router is allowlisted, **every user** can call `exactInputSingle` (or any other router entry point) and the extension will pass, regardless of whether that user is individually allowlisted.

---

### Impact Explanation

**Direct loss of allowlist policy on curated pools.** The `SwapAllowlistExtension` is the sole mechanism for restricting who may trade on a curated pool. A single allowlist entry for the router address collapses the per-user gate to an open gate for all users. Unauthorized users can execute swaps, drain liquidity at oracle-derived prices, and extract value from pools that were designed to be restricted. This matches the "High direct loss or curation failure if disallowed users can still trade" impact class defined in the contest scope.

---

### Likelihood Explanation

**Medium-High.** Any pool operator who deploys `SwapAllowlistExtension` and also wants to support the canonical `MetricOmmSimpleRouter` faces an unavoidable dilemma: either block all router-based swaps (breaking the standard UX) or allowlist the router (silently opening the gate to all users). The router is a first-party periphery contract documented as the standard swap entry point, so the allowlist-the-router path is the natural operational choice. No privileged attacker is required; any unprivileged user can exploit this by simply calling the router instead of the pool directly.

---

### Recommendation

**Short term:** In `SwapAllowlistExtension.beforeSwap`, check the `recipient` argument or require the pool to pass the original caller's address through a dedicated field. Alternatively, document that the extension is incompatible with the router and enforce this at the factory level by reverting pool creation that pairs `SwapAllowlistExtension` with a router-compatible configuration.

**Long term:** Redesign the extension interface so that the "economic actor" identity (the address that initiated the top-level transaction) is always propagated through the hook call chain, distinct from the immediate `msg.sender` of `pool.swap()`. This mirrors the fix recommended in the external report: the guard must check the actor the protocol actually intends to gate, not the intermediary that happens to be the direct caller.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, router, true)` to enable router-based swaps.
3. Pool admin does **not** call `setAllowedToSwap(pool, alice, true)` — Alice is not individually allowlisted.
4. Alice calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `SwapAllowlistExtension.beforeSwap` receives `sender = router`, checks `allowedSwapper[pool][router] == true`, and passes.
7. Alice's swap executes successfully despite never being allowlisted.

Contrast with a direct call: if Alice calls `pool.swap(...)` directly, `sender = alice`, `allowedSwapper[pool][alice] == false`, and the call reverts with `NotAllowedToSwap`. The bypass is exclusively available through the router path. [5](#0-4) [6](#0-5)

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
