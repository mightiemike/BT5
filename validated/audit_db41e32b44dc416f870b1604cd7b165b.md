### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Complete Allowlist Bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router contract address**, not the actual end user. If the router is allowlisted (which it must be for any router-based swap to succeed), every user — including those not individually allowlisted — can bypass the curated pool's access control.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` value is in the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap()` is the **router**, so `sender` delivered to `beforeSwap` is the router's address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The pool admin faces an inescapable dilemma:

| Router allowlisted? | Effect |
|---|---|
| Yes | Every user can swap through the router; per-user allowlist is nullified |
| No | No user can swap through the router at all; router is unusable for this pool |

There is no configuration that simultaneously allows specific users through the router while blocking others. The `sender` parameter is structurally the wrong actor to check for router-mediated swaps.

This is the direct analog to the ERC20Wrapper bug: in that case `msg.sender` was used instead of the `sender` parameter; here the `sender` parameter is used, but it resolves to the router (the wrong actor) rather than the actual economic principal.

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` to create a curated pool — for example, one restricted to KYC-verified counterparties or institutional LPs — cannot enforce that restriction when `MetricOmmSimpleRouter` is the entry point. Any unprivileged user can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router and trade against the pool's liquidity, bypassing the intended access control entirely. LP funds are exposed to counterparties the pool admin explicitly intended to exclude.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, documented periphery entry point for all swaps. Any user who knows the pool address can call it without any special privilege. The bypass requires no flash loans, no price manipulation, and no privileged role — only a standard router call. Likelihood is **High**.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the actual end user, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Router-side identity forwarding**: Require `MetricOmmSimpleRouter` to ABI-encode `msg.sender` (the actual user) into `extensionData` for each hop, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when `sender` is a known router.

2. **Recipient-based gating**: Change the check to `allowedSwapper[pool][recipient]` — the recipient is the address that receives output tokens and is typically the actual user. This is simpler but requires the pool admin to allowlist recipient addresses rather than initiator addresses.

Either way, the extension must be updated so that the checked address corresponds to the economic actor the pool admin intends to gate, not the intermediary contract that calls `pool.swap()`.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router must be allowlisted for any router swap
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is individually approved
  - bob is NOT in the allowlist

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool, recipient: bob, ...})
  2. Router calls pool.swap(bob, zeroForOne, amount, ...)
     → msg.sender inside pool.swap() = router
  3. Pool calls _beforeSwap(sender=router, recipient=bob, ...)
  4. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → true
  5. Swap executes; bob receives output tokens

Result: bob, a non-allowlisted user, successfully swaps on a curated pool.
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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
