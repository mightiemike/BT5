### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, allowing any unprivileged user to bypass the swap allowlist — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument (the direct caller of `MetricOmmPool.swap`) against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual user's address. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every registered extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted, using `msg.sender` (the pool) as the mapping key: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly: [3](#0-2) 

At that point `msg.sender` inside the pool is the **router**, so `sender` forwarded to the extension is the **router address**, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

The pool admin faces an impossible choice:
- **Allowlist the router** → every user can bypass the allowlist by routing through it.
- **Do not allowlist the router** → all router-mediated swaps are blocked, breaking normal UX.

There is no path that simultaneously allows router-mediated swaps and enforces per-user allowlist restrictions.

---

### Impact Explanation

Any user excluded from the allowlist can execute swaps in a restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The allowlist guard — the only mechanism preventing unauthorized swaps — is silently bypassed. Depending on the pool's purpose (e.g., institutional-only, KYC-gated, rate-limited), this constitutes unauthorized access to pool liquidity and direct loss of the protection the LP depositors relied upon when choosing a restricted pool.

---

### Likelihood Explanation

- The router is a public, permissionless contract that any user can call.
- Pool admins who want router-mediated swaps to work must allowlist the router; this is the expected operational configuration.
- No special knowledge or privileged access is required — any user who knows the pool is allowlist-gated can exploit this by simply using the router instead of calling the pool directly.
- The bypass is unconditional once the router is allowlisted.

---

### Recommendation

The extension must check the **originating user**, not the direct pool caller. Two viable approaches:

1. **Pass the originating user explicitly**: Add an `originSender` field to the `beforeSwap` hook arguments (or encode it in `extensionData`) so the router can forward `msg.sender` (the actual user) to the extension.

2. **Check `tx.origin` as a fallback** (weaker, but simpler): When `sender` is a known router, fall back to `tx.origin`. This is fragile and not recommended for production.

The cleanest fix is approach 1: the router should encode the real user in `extensionData`, and the extension should decode and check that identity when `sender` is a recognized router.

---

### Proof of Concept

```
Setup:
  1. Deploy MetricOmmPool with SwapAllowlistExtension registered in BEFORE_SWAP_ORDER.
  2. Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  3. Pool admin does NOT call setAllowedToSwap(pool, alice, true)

Attack:
  4. Alice (not allowlisted) calls:
       router.exactInputSingle({
           pool:        pool,
           recipient:   alice,
           zeroForOne:  true,
           amountIn:    X,
           ...
       })

  5. Router calls pool.swap(alice_recipient, true, X, ..., extensionData)
     → pool passes msg.sender = router as `sender` to _beforeSwap
     → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
     → swap proceeds; Alice receives output tokens

  6. Assert: alice's swap succeeded despite not being in the allowlist.
     Direct call pool.swap(...) from alice would revert with NotAllowedToSwap.
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
