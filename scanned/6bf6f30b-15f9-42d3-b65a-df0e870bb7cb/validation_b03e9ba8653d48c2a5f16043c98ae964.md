### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper — Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the **router**, not the user. If the pool admin allowlists the router address to enable router-based swaps, every user — including those not individually allowlisted — can bypass the curated-pool gate.

---

### Finding Description

**Call chain for a router swap:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, ..., extensionData)
              [msg.sender = router]
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        checks: allowedSwapper[pool][router]
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` (the direct caller) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever was passed — the router when the user entered through the periphery: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` inside the pool: [4](#0-3) 

The same substitution occurs in `exactInput` (all hops), `exactOutputSingle`, and the recursive `exactOutput` callback path: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants to allow allowlisted users to trade through the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** call that arrives through the router, regardless of who the originating user is. Any non-allowlisted address can bypass the curation gate by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) instead of calling `pool.swap` directly.

Conversely, if the admin does not allowlist the router, individually allowlisted users cannot use the router at all — breaking the expected user flow.

Both outcomes are fund-impacting: the bypass allows unauthorized traders to execute swaps against a pool whose liquidity was provisioned under the assumption that only vetted counterparties would trade.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who configures a `SwapAllowlistExtension` and also wants router support will naturally allowlist the router address, triggering the bypass. The attacker requires no special privilege — only the ability to call the public router.

---

### Recommendation

Pass the **originating user** through the swap path rather than the direct caller. Two concrete options:

1. **Preferred — pass `recipient` or an explicit `originator` field**: Extend the `swap` signature with an `originator` address that the router sets to `msg.sender` before calling the pool, and have the pool forward that value to extensions instead of (or in addition to) `msg.sender`.

2. **Alternative — check `sender` in the router context**: Have `SwapAllowlistExtension` accept an `originatorData` field inside `extensionData` that the router populates with the user's address, and verify it against the allowlist. This requires the router to sign the field and the extension to authenticate it.

Until fixed, pool admins should be warned that allowlisting the router address opens the gate to all users.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (beforeSwap order = extension 1).
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to enable router-based swaps for allowlisted users).
  - Alice is NOT individually allowlisted.

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool, ...}).
  2. Router calls pool.swap(recipient=Alice, ...) — msg.sender in pool = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. Alice trades on a pool she was never authorized to access.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds.
``` [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
