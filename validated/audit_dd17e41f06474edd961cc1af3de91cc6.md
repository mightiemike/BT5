Audit Report

## Title
SwapAllowlistExtension Bypass via Router: Wrong Identity Checked Allows Non-Allowlisted Users to Swap - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` binds to its own `msg.sender`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every user — including non-allowlisted ones — can bypass the swap gate by routing through the public, permissionless router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever was passed in: [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract: [3](#0-2) 

So the extension checks `allowedSwapper[pool][router]` — the router's allowlist status — not the end user's. A pool admin who wants to support router-mediated swaps for their allowlisted users must allowlist the router address. Once the router is allowlisted, the check passes for every caller regardless of their individual allowlist status, because the router is a public, permissionless contract that any address can call. The same bypass applies to `exactInput` and `exactOutputSingle`. [4](#0-3) [5](#0-4) 

## Impact Explanation
Any non-allowlisted user can bypass the `SwapAllowlistExtension` gate on a restricted pool by calling any router entry point. The pool receives and settles the swap normally; the extension never sees the real user's address. This directly breaks the core access-control invariant the extension is designed to enforce, allowing unrestricted swap access to a pool configured to be permissioned. Depending on the pool's purpose (e.g., institutional-only liquidity, regulatory-gated pools), this constitutes a direct loss of the access-control guarantee and enables unauthorized fund flows through the pool. [6](#0-5) 

## Likelihood Explanation
The trigger requires the pool admin to allowlist the router — a natural operational step when the pool is meant to be accessible via the standard periphery. Any user who discovers the router is allowlisted can immediately exploit the bypass with a single `exactInputSingle` call. No privileged access, no special tokens, and no malicious setup are required beyond the router being allowlisted. [7](#0-6) 

## Recommendation
The `sender` forwarded to extensions must represent the economic actor, not the intermediary contract. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should encode the real `msg.sender` (the end user) into `extensionData` so extensions can decode and check it. The extension interface already passes `extensionData` through to every hook.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode a trusted-caller-supplied user address from `extensionData` when `sender` is a known periphery contract, or the pool should expose a dedicated "originator" field that periphery contracts are required to populate.

Until fixed, pool admins should **not** allowlist the router address on permissioned pools; instead, allowlisted users must call the pool directly.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only allowed swapper
  allowedSwapper[pool][router] = true  // admin allowlists router to support alice's router usage

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
      → pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
          msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              allowedSwapper[pool][router] == true  ✓  (passes!)
        → swap executes normally

Result: bob swaps on a pool he is not allowlisted for.
``` [8](#0-7) [9](#0-8) [10](#0-9)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
