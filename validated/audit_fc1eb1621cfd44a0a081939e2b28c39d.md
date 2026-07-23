### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the extension sees the router's address — not the actual user's address. This is the wrong actor. If the router is allowlisted (a natural admin action for a "trusted intermediary"), every non-allowlisted user can bypass the curated pool's swap restriction by routing through the official router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the per-pool allowlist: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of the pool call: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. The actual user's allowlist entry is never consulted.

The same mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all of which call `pool.swap()` with `msg.sender = router`. [5](#0-4) 

---

### Impact Explanation

**Scenario A — Router is allowlisted (allowlist bypass):** A pool admin who wants to permit routing through the official router adds the router address to the allowlist. This silently opens the gate for every user, regardless of their individual allowlist status. Any non-allowlisted address can call `exactInputSingle` and trade on a curated pool that was supposed to restrict access. LP funds are exposed to actors the pool admin explicitly excluded.

**Scenario B — Router is not allowlisted (broken core flow):** A pool admin allowlists specific user addresses. Those users attempt to swap through the official router (the primary EOA-facing interface). The extension sees `sender = router`, finds it absent from the allowlist, and reverts with `NotAllowedToSwap`. Legitimate, allowlisted users cannot execute swaps through the supported periphery path — core swap functionality is broken for the intended audience.

Both outcomes are fund-impacting: Scenario A enables unauthorized value extraction from curated pools; Scenario B renders the allowlist-protected pool unusable for its intended participants.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface documented and deployed alongside the protocol. Pool admins configuring a `SwapAllowlistExtension` will naturally test direct pool calls, where `sender = user` and the check passes correctly. The mismatch only surfaces when users route through the router — the normal production path. The probability that a curated pool uses both the `SwapAllowlistExtension` and the official router is high, making this a realistic trigger condition.

---

### Recommendation

The extension must identify the economic actor, not the intermediary. Two options:

1. **Check `recipient` instead of `sender`** if the pool's design guarantees that `recipient` is the beneficiary of the swap. This is not always true (recipient can differ from the payer).

2. **Encode the real user in `extensionData` and verify it in the extension.** The router would include `msg.sender` in `extensionData`; the extension decodes and checks that address. This requires a coordinated convention between the router and the extension.

3. **Gate by `recipient`** and document that curated pools must set `recipient = msg.sender` at the router level, enforced by a wrapper.

The cleanest production fix is option 2: the router encodes the originating user in `extensionData`, and the extension decodes and checks that address against the allowlist, falling back to `sender` when no user override is present.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to permit routing (or alternatively, calls `setAllowedToSwap(pool, alice, true)` to permit only Alice).
3. **Bypass path:** Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`. The router calls `pool.swap(...)` with `msg.sender = router`. The extension checks `allowedSwapper[pool][router]` → `true` → swap succeeds. Bob trades on a pool he was supposed to be excluded from.
4. **Broken path (Scenario B):** Alice (allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`. Extension checks `allowedSwapper[pool][router]` → `false` → `NotAllowedToSwap` revert. Alice cannot use the official router despite being individually allowlisted. [3](#0-2) [6](#0-5)

### Citations

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
