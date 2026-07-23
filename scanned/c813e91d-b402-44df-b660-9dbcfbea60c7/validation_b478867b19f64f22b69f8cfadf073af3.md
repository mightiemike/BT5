### Title
SwapAllowlistExtension Gates Router Address Instead of Real User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the real user. If the pool admin allowlists the router to support router-mediated swaps, every unpermissioned user can bypass the curated allowlist by calling the router.

### Finding Description

The pool's `swap()` function passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` as itself: [4](#0-3) 

At that point `sender` arriving at the extension is the router address, not the real user. The extension therefore checks `allowedSwapper[pool][router]` — a single entry that, if true, grants every caller of the router unconditional access to the curated pool.

The `DepositAllowlistExtension` does not share this flaw because it gates by `owner`, which is an explicit argument that the liquidity adder sets to the real depositor: [5](#0-4) 

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router (the natural step to let approved users trade through the standard periphery) simultaneously opens the pool to every address on the network. Any unpermissioned user routes through `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`, the extension sees the allowlisted router as `sender`, and the guard passes. The curation invariant — only approved addresses may swap — is completely broken. This is a direct loss-of-policy impact on curated pools and constitutes broken core pool functionality.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical user-facing swap entry point. Pool admins who want approved users to be able to use the standard UI/router must allowlist the router. The documentation and NatSpec for `SwapAllowlistExtension` give no warning that allowlisting the router collapses the per-user gate. The trigger requires no special privilege: any unpermissioned address calls the public router.

### Recommendation

The extension must identify the real economic actor, not the intermediary. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that value. This requires a trusted encoding convention.
2. **Check `sender` only when `sender` is not a known router; otherwise check a user field from `extensionData`**: The extension can maintain a registry of trusted routers and, when `sender` is a router, require the real user identity to be present and signed in `extensionData`.

The simplest safe fix is to require direct pool calls for allowlisted pools (document that the router is incompatible with `SwapAllowlistExtension`) and revert if `sender` is a known router address.

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  pool admin calls swapExtension.setAllowedToSwap(pool, alice, true)
  pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
    // necessary so alice can use the router

Attack:
  bob (not on allowlist) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})

Execution trace:
  1. router.exactInputSingle() — msg.sender = bob
  2. router calls pool.swap(recipient, ...) — msg.sender at pool = router
  3. pool._beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap(sender=router, ...)
     checks: allowedSwapper[pool][router] == true  ✓
  5. guard passes; bob's swap executes on the curated pool

Result:
  bob, who is not on the allowlist, successfully swaps on a pool
  that was intended to be restricted to alice only.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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
