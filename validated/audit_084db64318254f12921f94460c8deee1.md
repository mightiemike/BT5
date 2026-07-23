### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any caller to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks whether the **router** is allowlisted, not the **end user**. A pool admin who allowlists the router to enable router-based swaps inadvertently opens the pool to every user of the router, defeating the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` to the pool: [4](#0-3) 

The same pattern holds for `exactInput` (every hop), `exactOutputSingle`, and the recursive `exactOutput` callback: [5](#0-4) 

The result: the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. If the pool admin allowlists the router (the natural step to enable router-based swaps), every user of the router can swap on the restricted pool regardless of whether they are individually allowlisted.

The interface NatSpec states the extension "Gates `swap` by swapper address, per pool": [6](#0-5) 

The implementation gates by the **direct caller of `pool.swap()`**, not the originating user. This is a semantic mismatch that makes the allowlist unenforceable whenever a public router is in the picture.

---

### Impact Explanation

A pool using `SwapAllowlistExtension` is typically a restricted venue — e.g., a private market-making pool where the LP provides tight spreads only to trusted counterparties. If the allowlist is bypassed:

- Any unpermissioned user can execute swaps at the favorable oracle-anchored rates the LP reserved for trusted parties.
- The LP suffers direct principal loss: tokens leave the pool at prices the LP never intended to offer to the general public.
- The loss scales with pool depth and the spread between the oracle price and the market price.

This meets the **direct loss of user principal / LP assets** impact gate.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router. This is the natural, expected configuration for any pool that wants to support router-based swaps while also restricting the universe of swappers. The admin has no way to simultaneously (a) allow router-mediated swaps and (b) enforce per-user restrictions, because the extension conflates the two. Any pool that attempts this configuration is fully exposed. Likelihood is **medium** (requires a specific but common admin configuration).

---

### Recommendation

Pass the **originating user** through the swap path rather than the direct caller. Two complementary fixes:

1. **Router**: store `msg.sender` (the end user) in transient storage alongside the callback context and expose it via a `swapOriginator()` view. Pass it as `callbackData` or a dedicated field so the pool can forward it to extensions.

2. **Extension**: change the allowlist key from `sender` (direct pool caller) to a verified originator field, or document clearly that the extension only gates direct `pool.swap()` callers and cannot be used with public routers.

Until fixed, pools that need per-user swap restrictions must not allowlist any public router and must require users to call `pool.swap()` directly.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only allowed user
  allowedSwapper[pool][router] = true         // admin enables router-based swaps

Attack (executed by bob, who is NOT allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
     → msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true ✓
  5. Swap executes; bob receives tokens at the oracle-anchored rate

Result:
  bob, who is not individually allowlisted, successfully swaps on the restricted pool.
  The LP's principal is transferred to bob at rates intended only for alice.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
