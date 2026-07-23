### Title
`SwapAllowlistExtension` gates the router address instead of the actual user on router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool. The pool always passes its own `msg.sender` as that argument. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, producing the exact wrong-actor binding described in the external report.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
              → MetricOmmPool._beforeSwap(msg.sender, ...)    // sender = router
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        checks: allowedSwapper[pool][router]  // ← wrong actor
```

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When the router calls the pool, the pool's `msg.sender` is the router contract, so `sender` arriving at the extension is the router address, not the user: [4](#0-3) 

The same misbinding applies to `exactOutputSingle`, `exactInput`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

Two fund-impacting outcomes depending on whether the router is allowlisted:

**Scenario A — router is allowlisted (allowlist bypass):**
A pool admin allowlists the router so that allowlisted users can reach the pool through the standard periphery. Because the extension checks `allowedSwapper[pool][router]` and the router is allowlisted, **every user** passes the check regardless of their own allowlist status. Any non-allowlisted address can swap on a curated pool by routing through `MetricOmmSimpleRouter`, completely defeating the curation policy. This is a direct loss of the access-control invariant the pool admin paid to enforce.

**Scenario B — router is not allowlisted (DoS for allowlisted users):**
If the router is not explicitly allowlisted, every router-mediated swap reverts with `NotAllowedToSwap` even for users who are individually allowlisted. Allowlisted users are forced to call the pool directly, losing slippage protection, multi-hop routing, and deadline enforcement provided by the router. This breaks the core swap flow for the intended user set.

Both outcomes are contest-relevant: Scenario A is a direct policy bypass with fund-impacting consequences on curated pools; Scenario B is broken core pool functionality causing an unusable swap flow.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented and deployed for the protocol. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the router is immediately affected. The trigger requires no special privilege: any user can call `exactInputSingle` or `exactOutputSingle`. The pool admin's only recourse is to never allowlist the router, which forces Scenario B.

---

### Recommendation

The extension must check the **economically relevant actor** — the user who initiated the swap — not the intermediary contract that called the pool. Two complementary fixes:

1. **In `SwapAllowlistExtension.beforeSwap`:** ignore the `sender` argument (which is the pool's `msg.sender`) and instead require callers to supply the real user address in `extensionData`, or accept that the check must be done at the pool level.

2. **Preferred — pool-level fix:** `MetricOmmPool.swap` should pass a dedicated `swapper` parameter (the originating user) separately from the callback payer, analogous to how `addLiquidity` separates `msg.sender` (payer) from `owner` (position holder). The extension would then receive the true user address as `sender`.

3. **Short-term mitigation:** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and must only be used on pools accessed directly.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// allowedSwapper[pool][allowedUser] = true
// allowedSwapper[pool][router]      = false  (or true — both are wrong)

// Attack (Scenario A — router allowlisted):
// allowedSwapper[pool][router] = true  (admin adds router to let allowedUser use it)

vm.prank(blockedUser);                          // NOT on the allowlist
router.exactInputSingle(ExactInputSingleParams({
    pool:    address(pool),
    tokenIn: address(token0),
    ...
    extensionData: ""
}));
// Pool receives msg.sender = router → extension checks allowedSwapper[pool][router] = true → PASSES
// blockedUser successfully swaps on a curated pool.

// Scenario B — router not allowlisted:
vm.prank(allowedUser);                          // IS on the allowlist
router.exactInputSingle(...);
// Pool receives msg.sender = router → extension checks allowedSwapper[pool][router] = false → REVERTS NotAllowedToSwap
// allowedUser cannot use the router at all.
``` [3](#0-2) [1](#0-0) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
