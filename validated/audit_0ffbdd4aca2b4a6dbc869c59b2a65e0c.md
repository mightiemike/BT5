### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which the pool sets to `msg.sender` of its own `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool admin who allowlists the router address to enable router-based swaps for their curated users inadvertently opens the pool to every unprivileged caller.

---

### Finding Description

**Pool → Extension argument binding**

`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value as `sender` to every configured extension.

**Extension check**

`SwapAllowlistExtension.beforeSwap` receives `sender` and checks it against the per-pool allowlist, using `msg.sender` (the pool) as the mapping key: [2](#0-1) 

**Router call path**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. Inside the pool, `msg.sender` is the router, so `sender` delivered to the extension is the router's address, not the originating user: [3](#0-2) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**The impossible configuration**

A pool admin who wants to allow only KYC'd users to swap, while also supporting the router, must allowlist the router address. But `allowedSwapper[pool][router] = true` passes the check for **every** caller of the router — the extension has no visibility into the originating `msg.sender` of the router call. The admin cannot simultaneously:
- Allow specific users to swap via the router, and
- Block other users from swapping via the router.

---

### Impact Explanation

Any unprivileged user can bypass a `SwapAllowlistExtension` guard on a curated pool by calling `MetricOmmSimpleRouter` instead of the pool directly, provided the pool admin has allowlisted the router (a natural and expected configuration step). The allowlist — the sole access-control mechanism for curated pools — becomes completely ineffective. Non-allowlisted users gain full swap access, breaking the pool's curation policy and potentially draining LP value through trades the pool was designed to restrict.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. A pool admin deploying a curated pool with `SwapAllowlistExtension` will naturally allowlist the router to let their approved users trade conveniently. The misconfiguration is not obvious from the extension's interface, and no documentation or NatSpec warns that allowlisting the router opens the pool to all callers. Any attacker who reads the extension code can exploit this immediately after the router is allowlisted.

---

### Recommendation

The extension must check the **economic actor** — the originating user — not the intermediary contract. Two complementary fixes:

1. **Pass the originating user through the router.** The router already stores the originating `msg.sender` in transient storage as the payer. Expose it in `callbackData` or a dedicated transient slot and have the pool forward it as a separate `originator` field to extensions.

2. **Alternatively, gate on `recipient` or require the router to pass the user address in `extensionData`.** The extension can decode the real user from `extensionData` when the caller is a known router, falling back to `sender` for direct calls.

Until fixed, pool admins should **not** allowlist the router address; instead, allowlisted users must call the pool directly.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (intending to let allowlisted users use the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool, ...}).
  2. Router calls pool.swap(recipient, ...) — msg.sender inside pool = router.
  3. Pool calls _beforeSwap(router, ...) → extension receives sender = router.
  4. Extension checks allowedSwapper[pool][router] → true → passes.
  5. Swap executes for attacker despite attacker never being allowlisted.

Result:
  - Attacker swaps on a curated pool that was supposed to block them.
  - SwapAllowlistExtension guard is completely bypassed.
``` [2](#0-1) [1](#0-0) [4](#0-3)

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
