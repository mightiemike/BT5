### Title
`SwapAllowlistExtension` gates the router's address instead of the actual user, allowing any user to bypass per-user swap restrictions via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is `msg.sender` of the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `sender` is the router contract address — not the actual user. If the router is allowlisted (which is required for any user to use it on that pool), every unprivileged user can bypass the per-user allowlist by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension caller) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`. [1](#0-0) 

In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the `sender` argument: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [3](#0-2) 

The actual user's address (`msg.sender` of the router call) is stored only in transient storage for the payment callback — it is **never forwarded to the pool or the extension**. The pool sees `msg.sender = router`, so `sender = router` reaches `SwapAllowlistExtension.beforeSwap`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

The same identity mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` intends to gate swaps to a specific set of addresses (e.g., KYC-verified traders). The `allowedSwapper` mapping is keyed per `(pool, swapper)`, expressing per-user intent. However, the admin faces an impossible choice:

- **Do not allowlist the router** → no user can swap through the router, even individually allowlisted ones.
- **Allowlist the router** → every user on-chain can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`, draining pool liquidity or executing trades the admin intended to block.

There is no configuration that allows specific users to swap through the router while blocking others. The guard is structurally bypassed for all router-mediated swaps.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical public swap interface. Any user who discovers the allowlist can trivially route through the router. No special privileges, flash loans, or oracle manipulation are required — a single `exactInputSingle` call suffices.

### Recommendation

Pass the originating user's address through the router to the pool (e.g., via `extensionData` or a dedicated field), and have `SwapAllowlistExtension` decode and check that address. Alternatively, the extension can maintain a separate allowlist for trusted routers and require that the router itself enforces per-user checks before calling the pool.

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`. `allowAllSwappers[pool] = false`. Only `alice` is in `allowedSwapper[pool]`.
2. Pool admin also calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can use the router.
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
5. Pool calls `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `bob` successfully swaps in a pool he was explicitly excluded from, bypassing the allowlist guard entirely. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-41)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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
