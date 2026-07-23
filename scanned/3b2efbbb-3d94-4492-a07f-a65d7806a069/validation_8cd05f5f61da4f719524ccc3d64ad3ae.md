### Title
`SwapAllowlistExtension` checks the router address instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address, not the originating user. A pool admin who allowlists the router address to enable router-mediated swaps for their curated users inadvertently opens the pool to every user who calls through the router, completely defeating the allowlist.

---

### Finding Description

**Hook binding in `SwapAllowlistExtension`:** [1](#0-0) 

The check is `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by the pool (the direct caller of `pool.swap()`).

**How the pool populates `sender`:** [2](#0-1) 

The pool passes `msg.sender` — its own direct caller — as `sender` to `_beforeSwap`.

**How `ExtensionCalling` forwards it:** [3](#0-2) 

`sender` is forwarded verbatim; no original-user context is preserved.

**How the router calls the pool:** [4](#0-3) 

The router calls `pool.swap()` directly. At the pool level, `msg.sender = router`. Therefore the extension receives `sender = router`, not the originating EOA.

**The bypass path:**

A pool admin who wants allowlisted users to be able to trade through the router must add the router to `allowedSwapper[pool][router]`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for every caller — allowlisted or not — because the extension cannot distinguish between router calls originating from different EOAs.

The same structural flaw applies to `exactInput` (multi-hop) and `exactOutput` paths: [5](#0-4) [6](#0-5) 

In all router entry points the pool's `msg.sender` is the router, so the extension always sees `sender = router`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd market makers, whitelisted institutions) is fully bypassed the moment the router is allowlisted. Any unpermissioned user can call `exactInputSingle` or `exactInput` through `MetricOmmSimpleRouter` and trade on the restricted pool. LP funds are exposed to adverse selection from actors the pool admin explicitly intended to exclude. This is a direct, fund-impacting curation failure.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router address. This is the natural and expected configuration step for any pool that wants to support router-mediated swaps for its allowlisted users — the admin has no other mechanism to enable router access. The condition is therefore reachable in every production deployment that combines `SwapAllowlistExtension` with `MetricOmmSimpleRouter`.

---

### Recommendation

The extension must gate the economically relevant actor — the originating user — not the intermediary contract. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a coordinated change to the router and extension.
2. **Check `sender` against a router-aware registry**: The extension maintains a mapping of trusted routers and, when `sender` is a known router, reads the actual user from a transient-storage slot set by the router before the pool call.

Either approach must be applied consistently to `exactInputSingle`, `exactInput`, `exactOutput`, and `exactOutputSingle`.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (BEFORE_SWAP_ORDER = 1)
  allowedSwapper[pool][userA]  = true   // intended allowlist
  allowedSwapper[pool][router] = true   // admin adds this to let userA use the router

Attack (userB, not allowlisted):
  1. userB calls router.exactInputSingle({pool: pool, tokenIn: T0, ...})
  2. router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
     → pool's msg.sender = router
  3. pool calls _beforeSwap(msg.sender=router, ...)
  4. ExtensionCalling encodes sender=router and calls SwapAllowlistExtension.beforeSwap
  5. Extension checks: allowedSwapper[pool][router] == true  → passes
  6. Swap executes; userB trades on the restricted pool

Result:
  userB, who is not in the allowlist, successfully swaps.
  The allowlist invariant is broken for every user who routes through MetricOmmSimpleRouter.
``` [7](#0-6) [8](#0-7) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-181)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint8 tradesLeftAfterThis = uint8(params.pools.length - 1);
    address pool = params.pools[tradesLeftAfterThis];
    bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, tradesLeftAfterThis);
    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _initCallbackContextforRecursiveOutput(
      pool, CALLBACK_MODE_EXACT_OUTPUT_ITERATE, tradesLeftAfterThis, msg.sender, params.tokens[0]
    );
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
