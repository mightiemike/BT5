### Title
`SwapAllowlistExtension` checks the router's address as `sender` instead of the actual user, allowing non-allowlisted users to bypass the swap gate via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool passes `msg.sender` (the immediate caller of `pool.swap()`) as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. The extension therefore checks the router's address, not the actual user's address. If the pool admin allowlists the router to enable router-mediated swaps, every user — including non-allowlisted ones — can bypass the curation gate.

---

### Finding Description

**Root cause in `MetricOmmPool.swap()`:** [1](#0-0) 

The pool passes `msg.sender` as the `sender` argument to `_beforeSwap`. When the call originates from `MetricOmmSimpleRouter`, `msg.sender` is the router contract address.

**Root cause in `SwapAllowlistExtension.beforeSwap()`:** [2](#0-1) 

The extension checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the first argument — the router's address, not the end user's address.

**Router call path — `exactInputSingle`:** [3](#0-2) 

The router calls `pool.swap(params.recipient, ...)` directly. The pool records `msg.sender = router`. The extension receives `sender = router`.

**Multi-hop `exactInput` — same problem on every hop:** [4](#0-3) 

For every hop, the router is `msg.sender` of the pool call, so the extension sees `sender = router` on all hops.

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` (the explicit position-owner argument, not the caller): [5](#0-4) 

The deposit extension is immune because `owner` is an explicit parameter that the pool preserves regardless of who calls `addLiquidity`. No equivalent "actual user" parameter exists on the swap path.

---

### Impact Explanation

Two fund-impacting outcomes arise from the same root cause:

**Scenario A — Allowlist bypass (High):** The pool admin allowlists the router address so that router-mediated swaps work for legitimate users. Because the extension checks `allowedSwapper[pool][router]`, every user — including those the admin explicitly excluded — can swap by calling `MetricOmmSimpleRouter.exactInputSingle()`. The curation gate is fully bypassed for all router-mediated swaps.

**Scenario B — Broken core functionality (Medium):** The pool admin allowlists individual user EOA/contract addresses but does not allowlist the router. Allowlisted users who call through the router have their swaps rejected (`NotAllowedToSwap`) because the extension sees `sender = router`, which is not in the allowlist. The official periphery path is unusable for any allowlisted pool.

Both outcomes break the invariant that a curated pool enforces the same allowlist policy regardless of which supported public entrypoint reaches it.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary production swap entrypoint. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the router will immediately encounter one of the two failure modes. No special timing, privileged access, or exotic token behavior is required — a standard `exactInputSingle` call is sufficient to trigger the bypass.

---

### Recommendation

Pass the economically relevant actor — the end user — through the swap path so the extension can gate on it. Two approaches:

1. **Add a `payer` or `originator` field to the swap call or extension data** that the router populates with `msg.sender` before calling the pool, and have the extension read that field.
2. **Mirror the deposit pattern**: have the pool accept an explicit `swapper` address parameter (analogous to `owner` in `addLiquidity`) that the router sets to `msg.sender`, and pass that to the extension as the identity to gate.

Until fixed, pools using `SwapAllowlistExtension` should not be deployed with the router as a supported entrypoint, or must accept that the allowlist is enforced only on direct pool calls.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension in beforeSwap order.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to allow router-mediated swaps for legitimate users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. Attacker bypasses the allowlist entirely.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
