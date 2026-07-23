### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is the **immediate caller of the pool's `swap()` function**. When `MetricOmmSimpleRouter` is used, `sender` is the router address, not the actual end-user. A pool admin who allowlists the router to enable router-based swaps for curated users inadvertently opens the pool to every user, because the extension never inspects the real originator.

---

### Finding Description

In `SwapAllowlistExtension.beforeSwap`:

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

`sender` is the first argument forwarded by the pool. Tracing the call chain:

1. `MetricOmmPool.swap()` is called; `msg.sender` there is the router.
2. `ExtensionCalling._beforeSwap` is invoked with `sender = msg.sender` of the pool call = **router address**.
3. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. [1](#0-0) [2](#0-1) [3](#0-2) 

The router stores the real user in transient storage for the payment callback, but never surfaces it to the pool or extension:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

This contrasts with `DepositAllowlistExtension`, which correctly checks `owner` (the position owner) rather than `sender` (the immediate caller), because the pool's `addLiquidity` explicitly separates payer from owner: [5](#0-4) 

---

### Impact Explanation

A pool admin who configures a curated pool with `SwapAllowlistExtension` and allowlists the router address (to let their approved users trade via the standard periphery) inadvertently grants swap access to **every user** who calls through the router. The curation policy is silently nullified. Non-allowlisted actors can execute swaps that the pool admin intended to block — e.g., toxic flow from uninformed or adversarial counterparties — causing direct LP principal loss on a pool whose entire value proposition is controlled access.

---

### Likelihood Explanation

Medium. The pool admin must allowlist the router, which is a natural and expected configuration step: without it, no allowlisted user can trade via the standard periphery either. The admin would reasonably expect the extension to still gate individual users after allowlisting the router, because the NatDoc states the extension "Gates `swap` by swapper address, per pool" — implying per-user granularity. The mismatch between that documented intent and the actual `sender`-based check is non-obvious.

---

### Recommendation

The `SwapAllowlistExtension` must identify the real originator, not the immediate pool caller. Two options:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.
2. **Check `sender` only for direct pool calls; reject router calls unless the router is explicitly trusted and the real user is verified**: Require the router to attest the user identity in a verifiable way (e.g., signed payload or transient-storage slot readable by the extension).

The `DepositAllowlistExtension` pattern (checking `owner`, not `sender`) is the correct model for user-level gating and should be mirrored in the swap path.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only `alice` should be able to swap.
3. Pool admin also calls `setAllowedToSwap(pool, router, true)` — to let `alice` use the standard router.
4. `bob` (not allowlisted) calls `router.exactInputSingle(...)` targeting the curated pool.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `ExtensionCalling._beforeSwap` passes `sender = router` to the extension.
7. The extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `bob`'s swap succeeds; the allowlist is bypassed entirely. [6](#0-5) [7](#0-6)

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
