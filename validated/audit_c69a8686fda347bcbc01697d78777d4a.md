### Title
`SwapAllowlistExtension` gates the router address instead of the real user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the value the pool passes as its first argument — which is `msg.sender` of the pool's own `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the **router contract**, not the end user. The allowlist therefore gates the router address, not the actual trader. Any pool admin who allowlists the router to support router-mediated swaps inadvertently opens the curated pool to every user on-chain.

---

### Finding Description

**Call chain when a user swaps via the router:**

```
user → MetricOmmSimpleRouter.exactInputSingle(params)
         └─ pool.swap(recipient, ...) [msg.sender = router]
               └─ _beforeSwap(msg.sender=router, recipient, ...)
                     └─ SwapAllowlistExtension.beforeSwap(sender=router, ...)
                           checks: allowedSwapper[pool][router]
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded — the router address when the swap entered through the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no mechanism to inject the original user's address into the `sender` slot: [4](#0-3) 

The same mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

**Allowlist bypass (High):** A curated pool (e.g., KYC-only, institutional-only) deploys with `SwapAllowlistExtension`. To support the official router, the pool admin calls `setAllowedToSwap(pool, router, true)`. From that moment, every user on-chain — including those the allowlist was designed to exclude — can call `router.exactInputSingle(...)` and pass the `allowedSwapper[pool][router]` check. The guard is completely neutralised for all router-mediated swaps, allowing disallowed traders to execute swaps against pool liquidity and extract value at oracle-anchored prices.

**Broken core functionality (Medium):** If the admin does not allowlist the router, every allowlisted user who attempts to swap through the router is rejected, making the official periphery path unusable for the curated pool.

Both outcomes are direct consequences of the wrong-actor binding: the economically relevant identity (the end user) is never the one checked.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented and deployed by the protocol. Any pool admin who enables the router for a curated pool — a natural and expected operational step — triggers the bypass. No special permissions, flash loans, or unusual token behaviour are required. A single `setAllowedToSwap(pool, router, true)` call is sufficient to open the pool to all users.

---

### Recommendation

Pass the **original user's address** through the call chain so the extension can gate the economically relevant actor. Two complementary approaches:

1. **Add a `payer`/`originator` field to the swap extension interface.** The pool stores the original `msg.sender` before any callback and passes it as a separate argument to `beforeSwap`/`afterSwap`. Extensions that need to gate the real user read this field instead of `sender`.

2. **Router-side: forward the original caller in `extensionData`.** The router encodes `msg.sender` into `extensionData` before calling the pool. The `SwapAllowlistExtension` decodes and verifies this value. This requires the extension to trust the router, so it should be combined with a router-address allowlist at the extension level.

The deposit-side analogue (`DepositAllowlistExtension`) does **not** share this bug because it checks `owner` (the position owner explicitly passed by the caller), not `sender`. [6](#0-5) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (intending to allow router-mediated swaps for allowlisted users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient, ...) [msg.sender = router]
  - pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes successfully for the attacker.

Expected: revert NotAllowedToSwap (attacker is not on the allowlist).
Actual:   swap succeeds; attacker trades on the curated pool.
```

The root cause is at: [7](#0-6) 

combined with the pool forwarding `msg.sender` (the router) as `sender`: [8](#0-7)

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
