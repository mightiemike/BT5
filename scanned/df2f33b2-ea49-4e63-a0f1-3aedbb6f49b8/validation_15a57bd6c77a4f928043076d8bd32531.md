### Title
`SwapAllowlistExtension` gates the router address instead of the real swapper, allowing any user to bypass the curated-pool allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool. The pool always sets that argument to `msg.sender` of the `swap` call. When a user enters through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the real user. The allowlist therefore gates the router address, not the actual swapper, making the guard trivially bypassable on any curated pool that accepts router-mediated swaps.

---

### Finding Description

**Call path for a direct swap (correct):**
```
user → pool.swap(...)
         msg.sender = user
         _beforeSwap(sender=user, ...)
         SwapAllowlistExtension: allowedSwapper[pool][user]  ✓
```

**Call path through the router (broken):**
```
user → MetricOmmSimpleRouter.exactInputSingle(...)
         router → pool.swap(recipient, ...)
                    msg.sender = router
                    _beforeSwap(sender=router, ...)
                    SwapAllowlistExtension: allowedSwapper[pool][router]  ✗
```

The pool's `swap` function unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router address when the user entered through the router: [3](#0-2) 

The router calls `pool.swap` directly, making itself the `msg.sender` to the pool: [4](#0-3) 

There are two concrete failure modes:

1. **Allowlist bypass:** If the pool admin allowlists the router address (a natural operational choice so that legitimate users can use the router), every user — including those explicitly denied — can swap through the router and pass the check, because the check resolves to `allowedSwapper[pool][router] == true`.

2. **Legitimate users blocked:** If the pool admin does not allowlist the router, every individually allowlisted user is blocked from using the router even though they are permitted to swap directly. The router is the primary user-facing entry point, so this effectively breaks the pool for all router users.

The same wrong-actor binding affects `exactOutputSingle`, `exactInput`, and `exactOutput` — all router entry points call `pool.swap` with the router as `msg.sender`. [5](#0-4) 

---

### Impact Explanation

On any curated pool that deploys `SwapAllowlistExtension` and expects to serve users through `MetricOmmSimpleRouter`, the allowlist is either universally bypassed (if the router is allowlisted) or universally broken for router users (if it is not). Either outcome constitutes a broken core pool functionality: the pool cannot simultaneously enforce its curated-access policy and support the standard periphery swap path. Disallowed users can execute swaps and receive output tokens they should not be able to obtain, directly violating the pool admin's access-control intent and potentially draining LP value to unauthorized counterparties.

---

### Likelihood Explanation

The router is the documented, primary user-facing entry point for swaps. Any user who discovers the discrepancy can bypass the allowlist by calling `exactInputSingle` instead of `pool.swap` directly. No privileged access, special tokens, or unusual setup is required — only a standard router call. The bypass is reachable on every curated pool that has `SwapAllowlistExtension` configured and does not explicitly block the router at the network level.

---

### Recommendation

The extension must recover the original user identity rather than relying on the `sender` argument, which reflects the immediate pool caller. Two sound approaches:

1. **Pass the real initiator explicitly.** Add a `swapInitiator` field to the `extensionData` payload that the router populates with `msg.sender` before calling the pool. The extension decodes and checks that field instead of `sender`. This requires a coordinated encoding convention between the router and the extension.

2. **Check `sender` against a router registry and fall through to a user-identity field.** If `sender` is a known router, decode the real user from `extensionData`; otherwise treat `sender` as the user. This is more complex but preserves backward compatibility with direct pool calls.

The simplest correct fix is option 1: the router encodes `msg.sender` into `extensionData`, and the extension decodes it as the authoritative swapper identity.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as `extension1` on the `beforeSwap` order.
2. Call `setAllowedToSwap(pool, router, true)` — the natural admin action to enable router-mediated swaps.
3. As a user address that has **not** been individually allowlisted, call `router.exactInputSingle(...)` targeting the pool.
4. Observe the swap succeeds: `allowedSwapper[pool][router] == true` passes the check even though the real user is not on the allowlist.
5. Alternatively, call `pool.swap(...)` directly as the same user and observe `NotAllowedToSwap` reverts — confirming the allowlist is enforced only on the direct path, not through the router. [6](#0-5) [7](#0-6)

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
