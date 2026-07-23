### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual user. If the pool admin allowlists the router address (a natural step to enable router-based swaps for their allowlisted users), every unpermissioned user can bypass the allowlist by calling through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The router does not forward the original caller's identity to the pool. Therefore, the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`.

A pool admin who wants allowlisted users to be able to swap via the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** call that arrives through the router, regardless of who the actual user is: [5](#0-4) 

The same structural mismatch exists for `exactInput`, `exactOutputSingle`, and `exactOutput` — all of them call `pool.swap()` from the router's address. [6](#0-5) 

---

### Impact Explanation

Any user who is **not** on the allowlist can bypass the `SwapAllowlistExtension` guard by routing through `MetricOmmSimpleRouter` whenever the router address has been allowlisted for that pool. This breaks the core invariant of the allowlist: that only explicitly permitted addresses may swap. Unauthorized swappers gain full access to the pool's liquidity at oracle-anchored prices, which can drain LP principal if the pool was designed for a restricted set of counterparties (e.g., a private market-making pool or a compliance-gated venue).

---

### Likelihood Explanation

The trigger requires the pool admin to have called `setAllowedToSwap(pool, router, true)`. This is a natural and expected administrative action: any admin who wants their allowlisted users to be able to use the standard router must allowlist the router. The admin's intent is to enable router access for their approved users, not to open the pool to everyone. The mismatch between intent and effect is non-obvious from the `setAllowedToSwap` API alone. The router is a public, permissionless contract, so once it is allowlisted, the bypass is reachable by any address with no further preconditions.

---

### Recommendation

The extension must check the **original user's identity**, not the intermediary's. Two complementary fixes:

1. **In `SwapAllowlistExtension.beforeSwap`**: check `recipient` (the second argument) or require the pool to forward the original user via `extensionData`. Alternatively, the extension should check both `sender` and `recipient` and require at least one to be allowlisted, depending on the intended semantics.

2. **In `MetricOmmSimpleRouter`**: pass the original `msg.sender` as part of `extensionData` so extensions can recover the true user identity. The router already stores the payer in transient storage (`_setNextCallbackContext`); the same pattern can be used to encode the originating user into `extensionData` before forwarding to the pool.

The cleanest fix is option 2: the router encodes `msg.sender` into `extensionData`, and `SwapAllowlistExtension` decodes and checks that value when present, falling back to `sender` for direct pool calls.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (beforeSwap order)
  allowedSwapper[pool][alice] = true          // alice is the only allowed user
  allowedSwapper[pool][router] = true         // admin enables router for alice

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})
      → router calls pool.swap(recipient=bob, ...)
        → pool calls _beforeSwap(sender=router, ...)
          → extension checks allowedSwapper[pool][router] == true  ✓
          → swap proceeds — bob's swap is not blocked

Result:
  Bob, who is not on the allowlist, successfully swaps against the
  restricted pool. The allowlist guard is fully bypassed.
``` [3](#0-2) [7](#0-6) [8](#0-7)

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
