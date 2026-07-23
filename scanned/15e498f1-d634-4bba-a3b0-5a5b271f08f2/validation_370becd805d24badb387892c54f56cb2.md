### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool receives the **router** as `msg.sender` and forwards the router address as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not whether the **actual user** is allowlisted. Any user can bypass a curated pool's allowlist by routing through the public router.

---

### Finding Description

**Actor binding in the pool → extension call chain**

`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the direct caller of the pool: [3](#0-2) 

**What the router sends to the pool**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly. The router is `msg.sender` inside the pool, so `sender` forwarded to the extension is the **router address**, not the originating user: [4](#0-3) 

The multihop path `exactInput` has the same issue and additionally uses `MetricOmmSwapPath.openLimit` (no per-hop price limit), compounding exposure: [5](#0-4) 

**Result of the mismatch**

The allowlist check resolves to `allowedSwapper[pool][routerAddress]`. Two broken outcomes follow:

1. **Bypass (primary impact):** If the pool admin allowlists the router address (the natural step to enable router-mediated swaps on a curated pool), every user on the public internet can call `exactInputSingle` and pass the extension check, regardless of whether they are individually allowlisted.
2. **Lockout (secondary impact):** If the router is not allowlisted, individually allowlisted users cannot swap through the router at all, breaking the intended user experience.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses. Because the extension sees the router's address instead of the user's address, the allowlist is either universally bypassed (router allowlisted) or universally broken for router users (router not allowlisted). In the bypass case, any unprivileged user can trade on a pool that was designed to exclude them, draining LP value at oracle-anchored prices with no slippage protection beyond the user's own `amountOutMinimum`.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported swap entrypoint documented in the periphery. Pool admins who configure `SwapAllowlistExtension` and want users to swap through the router will naturally allowlist the router address, triggering the bypass for all users. The attacker needs no special role, no privileged setup, and no non-standard token — only a call to the public router.

---

### Recommendation

The extension must gate the **originating user**, not the intermediary. Two options:

1. **Pass the real initiator through the router:** Have the router encode the originating `msg.sender` into `extensionData` and have the extension decode and verify it. This requires a trust assumption that the router is the only allowed intermediary.

2. **Check `sender` against a router-aware identity:** In `SwapAllowlistExtension.beforeSwap`, if `sender` is a known router, decode the real initiator from `extensionData` and check that address instead. The extension must validate that `sender` is a trusted router before trusting the decoded identity.

The cleanest fix is option 2 with an explicit trusted-router registry in the extension, so the allowlist always resolves to the economic actor regardless of which supported periphery path is used.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is NOT allowlisted

Attack:
  - alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes — alice bypasses the allowlist entirely.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
``` [3](#0-2) [6](#0-5) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
