### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual user. If the pool admin allowlists the router to support router-mediated swaps for legitimate users, any unprivileged user can bypass the allowlist entirely by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it unchanged to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` value and dispatches it to each extension in order: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router becomes `msg.sender` to the pool: [4](#0-3) 

So the extension receives `sender = router_address`, not the actual user. The allowlist check becomes `allowedSwapper[pool][router]`.

This creates an irresolvable dilemma for the pool admin:

- **Router NOT allowlisted**: All router-mediated swaps revert with `NotAllowedToSwap`, even for allowlisted users. Legitimate users are forced to call the pool directly.
- **Router IS allowlisted**: The check `allowedSwapper[pool][router]` passes for every caller, regardless of whether the actual user is on the allowlist. Any unprivileged user bypasses the gate by routing through the public router.

The pool admin cannot simultaneously allow legitimate users to use the supported periphery router and enforce the allowlist against unauthorized users.

---

### Impact Explanation

Any user can bypass the swap allowlist on a restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`). The allowlist invariant — that only approved addresses may trade on a curated pool — is broken. Unauthorized users gain access to pool liquidity, which can cause adverse selection for LPs (e.g., if the allowlist was intended to exclude informed traders or enforce KYC), constituting a broken core pool functionality and a direct policy bypass with fund-impacting consequences for LP positions.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router address. However, this is the natural and expected action for any pool admin who wants their allowlisted users to be able to use the supported periphery. The `MetricOmmSimpleRouter` is the primary user-facing swap interface; a pool admin who deploys a curated pool and then allowlists the router to support it inadvertently opens the allowlist to all users.

---

### Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor — the end user — not the intermediary router. Two approaches:

1. **Router forwards the original caller**: `MetricOmmSimpleRouter` encodes `msg.sender` into `extensionData` for each hop, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of `sender`.
2. **Extension checks both**: If `sender` is a known router, decode the real user from `extensionData`; otherwise check `sender` directly.

Either approach ensures the allowlist gates the same actor the pool admin intended to restrict, regardless of which supported periphery path reaches the pool.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin: setAllowedToSwap(pool, alice, true)
  pool admin: setAllowedToSwap(pool, router, true)   ← natural step to support periphery

Attack:
  Bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...)          [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓ passes
        → swap executes, Bob receives output tokens

Result:
  Bob swaps successfully on a pool he is not allowlisted for.
  allowedSwapper[pool][bob] == false, but the check was never applied.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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
