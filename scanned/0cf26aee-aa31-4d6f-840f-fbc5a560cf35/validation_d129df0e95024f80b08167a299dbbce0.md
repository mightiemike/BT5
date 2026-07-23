### Title
`SwapAllowlistExtension` gates the router address instead of the originating user, allowing any unprivileged swapper to bypass a curated pool's allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the originating user. A pool admin who allowlists the router (the only way to let any allowlisted user trade through the router) simultaneously opens the gate for every non-allowlisted user, completely defeating the per-user curation policy.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every registered extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist, keyed by `msg.sender` (the pool): [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [3](#0-2) 

At that point `msg.sender` of `pool.swap()` is the **router**, so the extension receives `sender = address(router)`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an impossible choice:

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | Blocked (unusable router) | Blocked |
| Yes | Allowed | **Also allowed — bypass** |

There is no configuration that lets allowlisted users use the router while blocking non-allowlisted users.

The analog to the IdRegistry bug is exact: just as `changeRecoveryAddress()` did not invalidate a pending signature that still referenced the old state, `setAllowedToSwap(pool, router, true)` does not encode any per-user identity — it opens the gate for the entire public, silently invalidating the curation invariant the admin believed they were maintaining.

---

### Impact Explanation

Any user who is **not** on the pool's swap allowlist can execute swaps on a curated pool by routing through `MetricOmmSimpleRouter`, provided the pool admin has allowlisted the router (which is the only way to let legitimate users use the router). The swap executes at the oracle-derived bid/ask, so the pool settles real token transfers. LP funds are exposed to trades from actors the pool was explicitly designed to exclude.

This is a direct loss-of-policy impact: the pool's curation boundary is broken, and every swap that should have been blocked instead settles against LP reserves.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the canonical user-facing entry point documented in the periphery.
- Any pool admin who wants allowlisted users to be able to use the router **must** allowlist the router address, triggering the bypass.
- No special privilege, flash loan, or unusual token is required — a standard `exactInputSingle` call suffices.
- The bypass is silent: the extension emits no event and the pool emits a normal `Swap` event.

---

### Recommendation

Pass the **originating user** through the swap call chain rather than the immediate `msg.sender`. Two concrete options:

1. **Add a `payer`/`originator` field to the swap parameters** that the router populates with `msg.sender` before calling `pool.swap()`, and have the pool forward it as a distinct argument to extensions alongside `sender`.

2. **Check `recipient` instead of `sender` in `SwapAllowlistExtension`** only if the protocol guarantees `recipient == originating user` — but this is not currently guaranteed (recipient can be an arbitrary address).

The cleanest fix is option 1: the pool passes both `sender` (the direct caller, used for callback settlement) and `originator` (the end user, used for policy checks), and `SwapAllowlistExtension` gates on `originator`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is KYC'd)
  allowedSwapper[pool][bob]   = false  (bob is not KYC'd)
  pool admin calls setAllowedToSwap(pool, router, true)
    → intent: let alice use the router
    → effect: allowedSwapper[pool][router] = true

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=bob, ...)
    → pool calls extension.beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes, bob receives tokens from LP reserves

Result:
  bob, who is explicitly not allowlisted, completes a swap
  against a pool whose entire purpose is to exclude him.
``` [2](#0-1) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
