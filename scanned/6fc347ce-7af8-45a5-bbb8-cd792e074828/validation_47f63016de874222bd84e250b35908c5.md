### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool. Because `MetricOmmPool.swap` passes `msg.sender` as `sender`, and the router is `msg.sender` when users route through `MetricOmmSimpleRouter`, the extension sees the router address — not the real user. A pool admin who allowlists the router to let their curated users access it simultaneously opens the gate to every user on the network.

---

### Finding Description

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern holds for `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

**Consequence — two mutually exclusive failure modes:**

| Pool admin intent | What admin must do | Actual result |
|---|---|---|
| Allow only `alice` to swap, including via router | Allowlist the router address | Every user on the network can bypass the allowlist through the router |
| Allow only `alice` to swap, direct calls only | Allowlist `alice`'s EOA | `alice` is blocked when she uses the router; the router is not allowlisted |

There is no configuration that simultaneously (a) lets allowlisted users use the router and (b) blocks non-allowlisted users from using the router. The extension's identity check is permanently bound to the wrong actor on the router path.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to a known set of counterparties. To let those counterparties use the standard router, the admin must call `setAllowedToSwap(pool, router, true)`. From that moment, any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` targeting the pool and the allowlist check passes — the extension sees the allowlisted router address, not the unauthorized caller. Unauthorized swaps drain LP-owned token reserves at oracle prices, causing direct loss of LP principal. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" impact criteria.

---

### Likelihood Explanation

The router is the canonical periphery entry point. Any pool admin who wants their allowlisted users to have a normal UX must allowlist the router, which is the exact configuration that opens the bypass. The attacker needs no special privilege — a single call to `exactInputSingle` with the target pool address suffices. The trigger is fully permissionless once the router is allowlisted.

---

### Recommendation

Pass the **original user** through the swap path rather than the immediate caller. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: store the original `msg.sender` in transient storage alongside the callback context (already done for the payer) and expose it as a `swapper` field in the extension data or as a dedicated transient slot. The pool or extension can then read the real initiator.

2. **In `SwapAllowlistExtension.beforeSwap`**: check `recipient` or a user-supplied identity field from `extensionData` rather than `sender` when the caller is a known router, **or** require the pool to forward the original initiator as a separate argument distinct from the immediate `msg.sender`.

The simplest safe fix is for the router to append the original `msg.sender` to `extensionData` and for `SwapAllowlistExtension` to decode and check that value when present, falling back to `sender` for direct pool calls.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)
  pool admin calls setAllowedToSwap(pool, router, true)   ← required for alice to use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution trace:
    router.exactInputSingle()          msg.sender = bob
      pool.swap(recipient=bob, ...)    msg.sender = router
        _beforeSwap(sender=router, ...)
          SwapAllowlistExtension.beforeSwap(sender=router)
            allowedSwapper[pool][router] == true  ← passes
        swap executes, LP funds transferred to bob

Result:
  bob swaps on a pool he was never authorized to access.
  LP principal is reduced by the swap output sent to bob.
``` [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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
