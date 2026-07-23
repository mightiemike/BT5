The call chain is now fully traced. Let me confirm the exact flow:

- `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` — `msg.sender` here is whoever called `pool.swap()`
- `ExtensionCalling._beforeSwap()` passes that `sender` to the extension
- `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool, `sender` = caller of `pool.swap()`
- When `MetricOmmSimpleRouter.exactInputSingle/exactInput/exactOutputSingle/exactOutput` calls `pool.swap()`, the pool's `msg.sender` is the **router**, so `sender` = router address

The identity mismatch is real and confirmed in code.

---

### Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension`, Allowing Any User to Bypass Per-User Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, the router is the immediate caller, so `sender` = router address. If the router is allowlisted (a natural operational choice), every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` = pool, `sender` = whoever called `pool.swap()`. When the router calls `pool.swap()`, `sender` = router address, not the end user. [4](#0-3) 

The router stores the actual user as `msg.sender` only in transient callback context for payment purposes — it is never forwarded to the pool as the swap initiator.

### Impact Explanation

Two concrete failure modes:

1. **Allowlist bypass**: Pool admin allowlists the router address (reasonable operational choice — the router is a trusted periphery contract). Any unprivileged user can now call `router.exactInputSingle/exactInput/exactOutputSingle/exactOutput` and swap in a pool that was intended to be restricted to specific addresses. The allowlist is completely defeated.

2. **Legitimate user lockout**: Pool admin allowlists specific user addresses but not the router. Those users cannot swap through the router at all — they must call `pool.swap()` directly, breaking the expected UX and integration path.

Scenario 1 is the high-impact path: an attacker who is not individually allowlisted routes through the router to trade in a restricted pool, receiving tokens they were not authorized to receive.

### Likelihood Explanation

- The router is the canonical swap interface; pool admins who want to allow "the router" will naturally allowlist the router address.
- No special setup is required beyond the router being allowlisted — any public user can call `exactInputSingle` with no preconditions.
- The bypass requires zero privileged access and works in every block.

### Recommendation

Pass the actual end-user identity through the call chain. Options:

1. Have the router encode the original `msg.sender` in `extensionData` and have the extension decode and verify it (requires a trusted router check inside the extension).
2. Add a `swapper` parameter distinct from `sender` to the hook interface, where `sender` = immediate caller and `swapper` = the user the router is acting on behalf of (passed via `extensionData` or a dedicated field).
3. Document that `SwapAllowlistExtension` gates the immediate caller only, and pool admins must allowlist the router rather than individual users — but then provide a separate per-user gate at the router level.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist the router
3. Pool admin does NOT allowlist attacker address.
4. Attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) — pool's msg.sender = router
6. Pool calls extension.beforeSwap(router, ...) — sender = router
7. allowedSwapper[pool][router] == true → check passes
8. Attacker's swap executes in a pool they were never individually authorized to access.
``` [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
