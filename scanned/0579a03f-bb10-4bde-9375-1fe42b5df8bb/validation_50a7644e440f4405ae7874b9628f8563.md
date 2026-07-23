### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User Identity, Allowing Any User to Bypass Per-User Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool passes `msg.sender` of `pool.swap()` as `sender`. When any user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of `pool.swap()`, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router address (a natural step to enable router-based swaps on a curated pool), every user — including those not individually allowlisted — can bypass the per-user gate.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is whatever the pool received as `msg.sender` of `pool.swap()`. [1](#0-0) 

**Pool passes its own `msg.sender` as `sender`:**

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

**Router calls `pool.swap()` without forwarding user identity:**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The router never passes the originating user's address into `pool.swap()`. The pool therefore sees `msg.sender = router`, and the extension evaluates `allowedSwapper[pool][router]` — a single boolean that covers every user of the router — rather than `allowedSwapper[pool][user]`.

The same collapse occurs for `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router address (to let approved users benefit from the router's deadline/slippage protection) inadvertently opens the pool to every user. Any address can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and execute swaps on the curated pool, bypassing the per-user allowlist entirely. This breaks the pool's access-control invariant and allows unauthorized parties to trade against LP assets in a pool that was explicitly configured to restrict access.

The inverse is equally broken: if the pool admin does **not** allowlist the router, then individually allowlisted users cannot use the router at all — they are forced to call `pool.swap()` directly, losing slippage and deadline protection.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported periphery path for end-user swaps. A pool admin who wants to run a curated pool and still support the standard router will naturally allowlist the router address. The system provides no documentation warning against this, and the `isAllowedToSwap` view function returns `true` for the router without revealing that this grants access to all users. The mistake is easy to make and the bypass requires no special privileges — any EOA can call the router.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **economic actor** (the end user), not the **call-chain intermediary** (the router). Two complementary fixes:

1. **Extension side:** The `beforeSwap` hook already receives both `sender` (direct caller of `pool.swap()`) and `recipient`. Add a dedicated `swapper` field to `extensionData` that the router populates with `msg.sender`, and have the extension verify and consume it. Alternatively, redesign the hook signature to carry the originating user explicitly.

2. **Router side:** The router should encode `msg.sender` into `extensionData` in a standard, verifiable way so that allowlist extensions can authenticate the true initiator regardless of the call depth.

Until this is resolved, pool admins must not allowlist the router address on curated pools; they must require users to call `pool.swap()` directly, which forfeits router safety features.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to enable router-based swaps for approved users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker (not individually allowlisted) calls:
      router.exactInputSingle(ExactInputSingleParams{
          pool:      curated_pool,
          recipient: attacker,
          ...
      })

Trace:
  router.exactInputSingle()
    → pool.swap(recipient=attacker, ...)   [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, attacker receives output tokens

Result:
  attacker successfully swaps on a pool that was configured to block them.
  The per-user allowlist is completely bypassed.
``` [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L165-181)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );
```
