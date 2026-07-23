### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap` call. When a user swaps through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual user. If the router is added to the allowlist (a natural configuration for pools that want to support router-mediated swaps for their curated users), any unprivileged user can bypass the allowlist entirely by routing through the router.

---

### Finding Description

**Root cause — wrong actor bound in `beforeSwap`:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the direct caller of `pool.swap`) is allowlisted: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap`: [3](#0-2) 

So the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual user's allowlist status is never consulted.

**The bypass path:**

A pool admin who wants to allow curated users to swap via the router must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, **any** address — including addresses the admin explicitly never allowlisted — can call `router.exactInputSingle(...)` and the extension will pass, because it only sees the router as `sender`.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` on the router, all of which call `pool.swap` with `msg.sender = router`. [4](#0-3) 

**Contrast with `DepositAllowlistExtension`:**

`DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position owner, the second argument), not `sender` (the adder contract). This is the correct pattern — the deposit allowlist gates the economically relevant actor. The swap allowlist does not follow this pattern. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd users, institutional LPs, or protocol-controlled addresses) can be bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The disallowed user receives output tokens from the pool at oracle-anchored prices, draining LP-owned liquidity that was intended to be accessible only to curated counterparties. This is a direct loss of LP principal and a broken core pool invariant (curated access control).

---

### Likelihood Explanation

The bypass requires the router to be in the allowlist. This is a natural and expected configuration: a pool admin who deploys a curated pool and wants their allowlisted users to be able to use the standard periphery router will add the router to `allowedSwapper`. The `MetricOmmSimpleRouter` is the canonical swap entry point documented in the periphery. Any pool admin who follows the natural integration path triggers the vulnerability. The trigger is unprivileged (any user can call the router).

---

### Recommendation

The `beforeSwap` hook should gate the **economic actor** — the address that initiated the transaction and will receive or pay tokens — not the intermediate contract. Two approaches:

1. **Pass the original initiator via `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` and the extension decodes and checks it. This requires a trusted encoding convention.

2. **Check `recipient` instead of `sender`**: For exact-input swaps the recipient is the user; for exact-output swaps the payer is the user. Neither is perfectly general across all swap modes.

3. **Structural fix**: Add a `tx.origin` check as a secondary gate (not recommended for general use but acceptable for allowlist-only pools where `tx.origin == msg.sender` is enforced by requiring direct EOA calls, blocking router paths entirely).

The cleanest fix is to document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this by checking `sender == tx.origin` (blocking contract callers), or to redesign the extension to accept a signed or forwarded user identity.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowAllSwappers[pool] = false
  - allowedSwapper[pool][alice] = true        // alice is curated
  - allowedSwapper[pool][router] = true       // admin adds router so alice can use it
  - allowedSwapper[pool][attacker] = false    // attacker is NOT curated

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({
       pool: pool,
       recipient: attacker,
       zeroForOne: true,
       amountIn: X,
       ...
     })
  2. Router calls pool.swap(recipient=attacker, ...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] == true → PASSES
  5. Swap executes; attacker receives output tokens from the curated pool

Result:
  attacker bypasses the allowlist and trades on a pool they were explicitly excluded from.
  allowedSwapper[pool][attacker] was never checked.
``` [2](#0-1) [6](#0-5) [7](#0-6)

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
