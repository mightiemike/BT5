### Title
`SwapAllowlistExtension` checks the router address instead of the real end-user, allowing any non-allowlisted user to bypass the swap allowlist — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user calls `MetricOmmPool.swap()` directly, `sender = msg.sender = user`. When a user calls `MetricOmmSimpleRouter.exactInputSingle/exactInput/exactOutputSingle/exactOutput`, the router calls `pool.swap()`, so `sender = router`. The allowlist check therefore gates the **router contract**, not the actual end-user. Any non-allowlisted user can bypass a curated pool's swap allowlist by routing through the public `MetricOmmSimpleRouter`.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user goes through `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Two broken outcomes:**

1. **Bypass path** — Pool admin allowlists the router (necessary for any allowlisted user to use the router). Every non-allowlisted user can now call `router.exactInputSingle()` and the check passes because the router is allowlisted.
2. **Broken functionality** — Pool admin does not allowlist the router. Allowlisted users cannot use the router at all; they must call the pool directly, defeating the purpose of the periphery layer.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in `MetricOmmSimpleRouter`. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool — the admin explicitly intends to restrict which addresses may trade. Any non-allowlisted user can bypass this restriction entirely by routing through the public `MetricOmmSimpleRouter`. This is a direct policy bypass with fund-impacting consequences: non-permitted users can drain liquidity from a pool that was designed to serve only a specific set of counterparties, causing direct loss of LP assets and breaking the core invariant of the allowlist guard.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any user who discovers the allowlist on a direct pool call will naturally try the router next. No special privileges, flash loans, or multi-transaction setup are required — a single `exactInputSingle` call suffices. Likelihood is **High**.

---

### Recommendation

The pool must forward the original end-user identity through the call stack so the extension can check it. Two complementary fixes:

1. **In `MetricOmmPool.swap()`**: Accept an explicit `swapper` parameter (the real end-user) separate from `msg.sender` (the immediate caller), and pass `swapper` to `_beforeSwap`. The pool should validate that `msg.sender` is an approved router/caller before trusting the supplied `swapper`.

2. **In `SwapAllowlistExtension.beforeSwap()`**: Until the pool propagates the real user, the extension can require that `sender == recipient` or that `sender` is not a known router — but this is fragile. The correct fix is at the pool/router level.

Alternatively, the router can pass the real user's address in `extensionData`, and the extension can decode and check it — but this requires the extension to trust the router, which reintroduces the same trust problem.

The cleanest fix mirrors how `DepositAllowlistExtension` handles the analogous case: it checks `owner` (the position beneficiary), not `sender` (the immediate caller). For swaps, the equivalent is the address that economically benefits — the `recipient` — or an explicit `originator` field added to the swap interface.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so that allowlisted users can use the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker (non-allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: pool,
           recipient: attacker,
           zeroForOne: true,
           amountIn: X,
           ...
       })

  2. Router calls pool.swap(attacker, true, X, ...) with msg.sender = router.

  3. Pool calls _beforeSwap(sender=router, ...).

  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
     Swap proceeds.

  5. attacker receives output tokens despite never being allowlisted.

Result: The swap allowlist is fully bypassed. The attacker trades on a
curated pool that was designed to exclude them, causing direct LP loss.
``` [6](#0-5) [7](#0-6) [4](#0-3)

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
