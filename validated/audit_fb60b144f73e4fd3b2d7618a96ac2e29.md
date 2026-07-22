### Title
SwapAllowlistExtension Checks Router Address as Swapper, Allowing Any User to Bypass Per-User Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps, every user—including those not individually allowlisted—can bypass the per-user gate by calling any `exact*` function on the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against `allowedSwapper[msg.sender][sender]` (where `msg.sender` is the calling pool): [1](#0-0) 

The pool passes its own `msg.sender` (the direct caller of `pool.swap()`) as the `sender` argument to the extension: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly, making `msg.sender` of `pool.swap()` equal to the router address: [3](#0-2) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. If the pool admin allowlists the router address (e.g., to allow allowlisted users to use the router), then `allowedSwapper[pool][router] = true`, and the check passes for **any** user who routes through the router—regardless of whether that user is individually allowlisted.

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

The allowlist is keyed by pool and swapper address, with no mechanism to propagate the original EOA through the router hop: [5](#0-4) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` as a `beforeSwap` hook intends to restrict swaps to a curated set of addresses. Once the pool admin allowlists the router (the natural step to let allowlisted users use the standard periphery), the allowlist is effectively open to every user. Non-allowlisted users can execute swaps on the curated pool, draining LP funds at oracle-derived prices and violating the pool's intended access policy. The bypass is permanent for as long as the router remains allowlisted.

Additionally, if the admin does **not** allowlist the router, allowlisted users cannot use `MetricOmmSimpleRouter` at all—breaking the core swap flow for the standard periphery path.

### Likelihood Explanation

The pool admin must allowlist the router for the bypass to be reachable. This is a plausible operational step: allowlisted users need the router for multi-hop swaps or slippage-protected single-hop swaps, and the admin may allowlist the router address without realizing it grants unrestricted access to all users. The `setAllowedToSwap` setter requires only pool-admin privilege: [6](#0-5) 

No other preconditions are required. Any user with knowledge of the router address can exploit this immediately after the router is allowlisted.

### Recommendation

The `SwapAllowlistExtension` should gate the economically relevant actor—the original EOA—not the direct caller of `pool.swap()`. Two viable approaches:

1. **Extension-data forwarding**: The router encodes the original `msg.sender` into `extensionData`; the extension verifies the router's identity via `msg.sender` (the pool) and then reads the original user from the payload. This requires the router to be a trusted forwarder.
2. **Recipient-based gating**: Gate on `recipient` instead of `sender` when the pool is configured for router use, since `recipient` is the address that receives output tokens and is set by the original user.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin allowlists userA:  setAllowedToSwap(pool, userA, true)
3. Admin allowlists router: setAllowedToSwap(pool, router, true)
   (to let userA use the router)
4. userB (not allowlisted) calls:
     router.exactInputSingle({pool: pool, recipient: userB, ...})
5. Router calls pool.swap() — msg.sender of pool.swap() = router.
6. Pool calls extension.beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] == true → passes.
8. userB receives output tokens from the curated pool.
   allowedSwapper[pool][userB] was never set; the allowlist is bypassed.
``` [1](#0-0) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-20)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
