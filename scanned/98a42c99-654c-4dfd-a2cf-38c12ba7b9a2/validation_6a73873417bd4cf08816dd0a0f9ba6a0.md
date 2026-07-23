### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` from the pool's perspective — the **direct caller of `pool.swap()`**. When a swap is routed through `MetricOmmSimpleRouter`, that direct caller is the router contract, not the end user. The allowlist therefore gates the router address, not the economic actor. If the router is allowlisted (a natural admin action to enable router-based trading), every user — including those explicitly excluded — can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router**, so `sender` delivered to the extension is the router address. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same substitution occurs for every hop in `exactInput` multi-hop paths: [5](#0-4) 

This is the direct analog to the Yearn `_creditAvailable` / `_debtOutstanding` split: two reachable paths that should enforce the same identity check use different reference points — direct-swap path checks the real user, router path checks the router — so the guard is inconsistently applied depending on entry point.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses. To also allow those addresses to trade through the router (the standard UX path), the admin must add the router to the allowlist. Once the router is allowlisted, **any address** — including those the admin explicitly excluded — can call `router.exactInputSingle` and pass the allowlist check, because the check resolves to `allowedSwapper[pool][router] == true`. Unauthorized users can trade against LP positions that were meant to be accessible only to trusted counterparties, extracting value from LPs who accepted risk under the assumption of a restricted trading set.

---

### Likelihood Explanation

The router is the standard, documented entry point for swaps. A pool admin who wants allowlisted users to trade via the router will naturally add the router to the allowlist. The bypass requires no special privilege, no malicious setup, and no non-standard token — only a call to a public router function. Any user who observes the router is allowlisted can exploit this immediately.

---

### Recommendation

The `beforeSwap` hook should gate the **economic actor**, not the immediate caller. Two complementary fixes:

1. **Short term:** In `SwapAllowlistExtension.beforeSwap`, check `tx.origin` as a fallback when `sender` is a known router, or require the router to forward the original `msg.sender` in `extensionData` and verify it there.

2. **Long term (preferred):** Redesign the router to pass the originating user as the `sender` argument to `pool.swap`, or add a dedicated `swapFor(address onBehalfOf, ...)` entry point that the pool records as the canonical swapper identity. The allowlist extension should then gate that canonical identity regardless of the call path.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  admin calls setAllowedToSwap(pool, router, true)  // router added so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: bob})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...)   [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
        → swap executes, bob receives tokens

  Result: bob bypasses the allowlist and trades on a restricted pool.
```

The allowlist check resolves to `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][bob]`, so the guard is silently satisfied and the swap settles in full. [6](#0-5) [7](#0-6) [4](#0-3)

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
