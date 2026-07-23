Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool, which is the pool's immediate `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router is allowlisted (required for any router-mediated swap to function), every non-allowlisted user can bypass the curated pool's allowlist by calling the router.

## Finding Description
**Hook argument binding:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

**What the extension checks:**

`SwapAllowlistExtension.beforeSwap` receives `sender` (the pool's caller) and checks it against the per-pool allowlist, using `msg.sender` (the pool) as the namespace key: [3](#0-2) 

When a user calls the pool directly, `sender = user` and the check is `allowedSwapper[pool][user]` — correct.

**Router path breaks the identity:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the pool's `msg.sender` the router contract, not the original caller: [4](#0-3) 

The pool therefore passes `sender = router` to the extension. The extension evaluates `allowedSwapper[pool][router]`. The original end-user identity is never seen by the guard.

The same substitution occurs in `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`: [5](#0-4) [6](#0-5) [7](#0-6) 

**Why existing guards fail:**

There is no secondary check on the originating user. The `extensionData` field passed by the router is `""` (empty string) in `exactInputSingle` and `exactOutputSingle`, and user-supplied in multi-hop paths — neither is validated by the extension. The extension has no mechanism to distinguish a direct call from a router-mediated call, and no way to recover the original caller.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` faces an inescapable dilemma: if the router is not allowlisted, allowlisted users cannot use the standard periphery router at all (broken core swap flow); if the router is allowlisted (the normal operating mode), every non-allowlisted user bypasses the guard by calling the router. This is a direct admin-boundary break: the pool admin's explicit curation policy is nullified by an unprivileged path through the canonical periphery. Depending on pool design, this exposes LP funds to trades from counterparties the pool admin explicitly excluded (e.g., MEV bots, sanctioned addresses, or competitors in a private market-making pool).

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical, publicly deployed periphery swap path. No special privilege or setup is required — any EOA or contract can call `exactInputSingle`. The bypass is deterministic and requires zero oracle manipulation or timing. A pool admin who wants router support (the normal operating mode) must allowlist the router, which immediately opens the bypass to all users.

## Recommendation
The extension must gate on the economic actor (the end user), not the transport layer (the router). Two viable approaches:

1. **Pass original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and verifies it. This requires a trusted router assumption and a matching extension implementation.

2. **Dedicated router allowlist + per-user check in extension data**: The extension distinguishes direct calls (check `sender`) from router calls (decode user from `extensionData` and check that). The router must be a known, trusted contract.

The simplest safe fix is to require that `extensionData` always carries the verified end-user address when the caller is a known router, and have the extension enforce that binding.

## Proof of Concept
```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension (beforeSwap order = extension 1)
  admin calls SwapAllowlistExtension.setAllowedToSwap(pool, router, true)
    // router must be allowlisted for any router-mediated swap to work
  admin does NOT allowlist Alice

Attack:
  Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes — Alice bypassed the allowlist

Verification:
  Alice calling pool.swap() directly would revert:
    → extension checks allowedSwapper[pool][alice] == false  ✗  → NotAllowedToSwap
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37, where `sender` is the pool's immediate caller rather than the originating user: [8](#0-7)

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
