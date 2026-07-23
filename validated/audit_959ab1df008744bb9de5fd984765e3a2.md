### Title
`SwapAllowlistExtension` checks router address instead of end-user, allowing any caller to bypass the per-user swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap()` receives `sender` from the pool, which is `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual end user. If the router is allowlisted, every unprivileged user who calls through the router bypasses the per-user swap gate entirely.

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`_beforeSwap()` forwards this value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks whether that `sender` is in the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

So `msg.sender` to the pool is the **router contract**, not the end user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

**Bypass path**: If the pool admin allowlists the `MetricOmmSimpleRouter` address (a natural step to enable router-based swaps), then `allowedSwapper[pool][router] == true` for every call that arrives through the router — regardless of who the actual caller is. Any unprivileged address can call `router.exactInputSingle()` and the extension passes.

**Broken-allowlist path (secondary)**: If the admin allowlists individual user addresses but not the router, allowlisted users who call through the router are incorrectly blocked, because the extension sees the router address and finds no match.

Both paths stem from the same root cause: `sender` in the swap hook is the direct caller of `pool.swap()`, which is the router, not the human swapper.

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified traders, institutional market makers, or whitelisted bots). Once the router is allowlisted, the restriction is nullified for all router-routed calls. Any unprivileged user can execute swaps against the restricted pool, draining LP assets at oracle-derived prices or executing trades the pool admin explicitly intended to prevent. This breaks the core allowlist invariant and constitutes a direct loss of LP principal through unauthorized swap execution.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who wants to allow router-based swaps for allowlisted users will naturally add the router to the allowlist — triggering the bypass for all users simultaneously. The trigger requires no special privilege: any EOA calling `exactInputSingle` or `exactInput` through the router is sufficient.

### Recommendation

The extension must check the actual end user, not the intermediary. Two options:

1. **Pass the real user via `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and check it. This requires a convention between router and extension.
2. **Check `recipient` instead of `sender`**: For swap allowlists, checking the `recipient` (the address receiving output tokens) is a closer proxy to the actual beneficiary, though it can also be set arbitrarily.
3. **Preferred — dedicated user-identity field**: Add a `payer` or `originator` field to the swap hook parameters that the pool populates from callback context (transient storage already tracks the payer in the router), giving extensions access to the true initiator.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls extension.setAllowedToSwap(pool, address(router), true)
    → intending to allow router-based swaps for allowlisted users
  pool admin does NOT add attacker address to allowlist

Attack:
  attacker (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: token0, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient, zeroForOne, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → extension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ← passes!
        → swap executes, tokens transferred to attacker's recipient

Result:
  Attacker successfully swaps against a pool they are not individually
  authorized to access. The allowlist guard is fully bypassed.
``` [3](#0-2) [5](#0-4) [6](#0-5)

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
