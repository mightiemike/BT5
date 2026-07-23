### Title
`SwapAllowlistExtension` gates on the router's address instead of the end-user's address, making the allowlist bypassable via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This wrong-actor binding produces two broken outcomes: (1) allowlisted users cannot swap through the supported router at all, and (2) if the pool admin allowlists the router to fix (1), every user — including non-allowlisted ones — can bypass the curated-pool gate.

---

### Finding Description

**Call chain for a router swap:**

```
user → MetricOmmSimpleRouter.exactInputSingle(...)
         → pool.swap(recipient, ...)          // msg.sender at pool = router
             → _beforeSwap(msg.sender=router, ...)
                 → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                     → allowedSwapper[pool][router]   ← wrong actor checked
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that value against the allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct — it namespaces the mapping) and `sender` is the router (wrong — it should be the end user).

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` (the economically relevant actor explicitly passed by the caller), not `sender` (the immediate pool caller):

```solidity
// DepositAllowlistExtension.sol line 38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The swap extension has no equivalent "end-user" parameter to check, so it falls back to the router's address.

---

### Impact Explanation

**Broken path (router not allowlisted):** A pool admin allowlists `alice` and `bob` for a curated pool. Both users attempt to swap via `MetricOmmSimpleRouter.exactInputSingle`. The extension sees `sender = router`, which is not allowlisted, and reverts `NotAllowedToSwap`. The supported periphery swap path is completely unusable for allowlisted users — broken core pool functionality.

**Bypass path (router allowlisted to fix the above):** The admin, wanting allowlisted users to use the router, adds the router to `allowedSwapper[pool]`. Now `charlie` (not allowlisted) calls `router.exactInputSingle(...)`. The extension sees `sender = router`, which is allowlisted, and the swap succeeds. The curated-pool gate is fully bypassed for any user who routes through the router. Non-allowlisted users can trade in a pool that was intended to be restricted, violating the pool's access-control invariant and potentially extracting value from LP positions sized for a controlled set of counterparties.

---

### Likelihood Explanation

The broken-path consequence (allowlisted users blocked from the router) is immediate and requires no admin error — it is a direct result of the wrong-actor binding. The bypass path requires the admin to allowlist the router, which is the natural corrective action a pool admin would take after discovering that allowlisted users cannot use the router. The two outcomes are therefore causally linked: fixing the usability problem creates the security failure.

---

### Recommendation

Pass the originating user's address through the swap path so the extension can check the correct actor. One approach: add a `swapper` field to `extensionData` that the router populates with `msg.sender` before calling the pool, and have the extension decode and verify it. A simpler alternative is to redesign `beforeSwap` to receive a dedicated `originator` parameter (analogous to `owner` in `beforeAddLiquidity`) that the pool sets to the address the admin intends to gate. Until then, pool admins must be warned that `SwapAllowlistExtension` cannot safely coexist with the router on a curated pool.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, alice, true).
   → alice is the only allowlisted swapper.

3. alice calls router.exactInputSingle({pool: pool, ...}).
   → router calls pool.swap(recipient, ...) — msg.sender at pool = router.
   → extension checks allowedSwapper[pool][router] → false → REVERT NotAllowedToSwap.
   → alice cannot use the router despite being allowlisted. (broken functionality)

4. Admin, to fix alice's problem, calls setAllowedToSwap(pool, router, true).

5. charlie (not allowlisted) calls router.exactInputSingle({pool: pool, ...}).
   → router calls pool.swap(recipient, ...) — msg.sender at pool = router.
   → extension checks allowedSwapper[pool][router] → true → PASS.
   → charlie swaps successfully in a pool that was supposed to be restricted. (bypass)
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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
