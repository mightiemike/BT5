### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real User, Allowing Allowlist Bypass or Locking Allowlisted Users Out of the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. The extension therefore gates the router address, not the individual swapper. This produces two mutually exclusive broken states: (1) allowlisted users cannot swap via the router because the router is not in the allowlist, or (2) if the admin allowlists the router to fix (1), every unprivileged user can bypass the individual allowlist entirely.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` with itself as `msg.sender`: [4](#0-3) 

The extension therefore receives `sender = router address`. The actual end-user identity is never visible to the hook.

### Impact Explanation

Two fund-impacting outcomes follow directly:

**Outcome A – Allowlisted users locked out of the router.** The pool admin allowlists `userA` by address. `userA` calls `router.exactInputSingle`. The router calls `pool.swap`; the extension checks `allowedSwapper[pool][router]`; the router is not allowlisted; the swap reverts. `userA` must call the pool directly, making the router unusable for any individually-gated pool. This is a broken core swap flow.

**Outcome B – Full allowlist bypass.** To fix Outcome A, the admin allowlists the router address. Now `allowedSwapper[pool][router] = true`. Any unprivileged `userB` (not individually allowlisted) calls `router.exactInputSingle`; the extension sees `sender = router`; the check passes; `userB` swaps freely in a pool that was supposed to be restricted. The admin-configured access boundary is fully bypassed by any public user who routes through the router.

### Likelihood Explanation

The router is the primary UX entry point for swaps. Any pool that deploys `SwapAllowlistExtension` to gate individual users will immediately encounter Outcome A (allowlisted users cannot use the router). The natural remediation an admin would attempt is to allowlist the router, which triggers Outcome B. No special privilege or unusual setup is required beyond using the standard periphery router.

### Recommendation

The extension must gate the **economic actor**, not the intermediary. Two options:

1. **Pass the original user through the router.** Add a `swapperOverride` field to `extensionData` that the router populates with `msg.sender` before calling the pool. The extension reads and validates this field only when `sender` is a known trusted router (verified against the factory).

2. **Check `sender` in the extension against a router registry.** When `sender` is a registered router, require the extension data to carry a signed or factory-verified user identity; otherwise fall back to `sender` directly.

Either way, the extension must never treat the router address as the identity to gate.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][userA] = true   // only userA is allowed
  allowedSwapper[pool][router] = false // router not listed

Step 1 – Outcome A (allowlisted user blocked):
  userA calls router.exactInputSingle(pool, ...)
  router calls pool.swap(recipient, ...) → msg.sender = router
  extension checks allowedSwapper[pool][router] → false → revert NotAllowedToSwap
  userA cannot use the router despite being allowlisted.

Admin "fix": allowedSwapper[pool][router] = true

Step 2 – Outcome B (bypass):
  userB (not allowlisted) calls router.exactInputSingle(pool, ...)
  router calls pool.swap(recipient, ...) → msg.sender = router
  extension checks allowedSwapper[pool][router] → true → passes
  userB swaps freely, allowlist fully bypassed.
``` [3](#0-2) [5](#0-4) [6](#0-5) [7](#0-6)

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
