### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` parameter, which is `msg.sender` of the pool's `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` equals the **router address**, not the actual user. A pool admin who allowlists the router to enable their allowlisted users to use the standard interface inadvertently opens the pool to **all users**, completely bypassing the per-user allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

```solidity
_beforeSwap(
  msg.sender,   // sender = immediate caller of pool.swap()
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

`SwapAllowlistExtension.beforeSwap()` then checks this `sender` against the per-pool allowlist, where `msg.sender` is the pool: [2](#0-1) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
```

When `MetricOmmSimpleRouter.exactInputSingle()` is used, the router calls `pool.swap(params.recipient, ...)`: [3](#0-2) 

```solidity
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

The router is `msg.sender` to the pool. The pool therefore passes `sender = router` to `_beforeSwap`. The extension checks `allowedSwapper[pool][router]`, **not** `allowedSwapper[pool][actualUser]`.

A pool admin who wants their allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **any user** can call `router.exactInputSingle()` and the extension will pass — because `sender = router` is allowlisted — regardless of whether that user is individually allowlisted. [4](#0-3) 

The pool admin has no mechanism to simultaneously:
- Allow their allowlisted users to use the router, AND
- Block non-allowlisted users from using the router.

The same `exactInput` multi-hop path has the same flaw for intermediate hops: [5](#0-4) 

---

### Impact Explanation

The swap allowlist protection is completely bypassed for all router-mediated swaps once the router is allowlisted. Any user can trade on a supposedly curated/restricted pool by routing through `MetricOmmSimpleRouter`. LP funds are at direct risk if the pool was designed to only accept specific, trusted counterparties (e.g., institutional traders, KYC-verified users). The pool admin cannot fix this without removing the router from the allowlist entirely, which then breaks the router path for legitimately allowlisted users as well.

---

### Likelihood Explanation

A pool admin who deploys a curated pool with a swap allowlist and also wants their allowlisted users to use the standard router interface will naturally call `setAllowedToSwap(pool, router, true)`. This is a common and expected operational step. The bypass is non-obvious because the pool admin's mental model is "I am allowlisting the router so my users can use it," not "I am opening the pool to all users." The `SwapAllowlistExtension` documentation says it "Gates `swap` by swapper address, per pool" — the word "swapper" is ambiguous and does not warn that the checked identity is the immediate pool caller, not the economic actor. [6](#0-5) 

---

### Recommendation

The extension must check the actual economic actor (the user who initiated the transaction), not the immediate caller of the pool. Concrete options:

1. **Router-forwarded identity**: The router passes the original `msg.sender` as part of `extensionData`; the extension decodes and verifies it. This requires a trusted router convention.
2. **Pool-level original sender**: The pool exposes a separate `originalSender` field that periphery contracts populate, and the extension reads it.
3. **Documentation + safe default**: Clearly document that allowlisting the router opens the pool to all users. Provide a warning in `setAllowedToSwap` when the target address is a known router. Recommend that curated pools never allowlist the router and instead require direct pool calls.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin: setAllowedToSwap(pool, user1, true)
   — intent: only user1 may swap.
3. Pool admin: setAllowedToSwap(pool, router, true)
   — intent: allow user1 to use the router conveniently.
4. Non-allowlisted user2 calls:
     router.exactInputSingle({pool: pool, recipient: user2, ...})
5. Router calls: pool.swap(user2, ...)
   — router is msg.sender to the pool.
6. Pool calls: _beforeSwap(sender=router, ...)
7. Extension checks: allowedSwapper[pool][router] → true
   — router is allowlisted, check passes.
8. Swap executes for user2 despite user2 not being individually allowlisted.
   — Allowlist completely bypassed.
``` [2](#0-1) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-25)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
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
