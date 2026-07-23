Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of original user, enabling allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` always sets to its own `msg.sender`. When users route through `MetricOmmSimpleRouter`, the pool receives the router address as `msg.sender`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][originalUser]`. This creates an irresolvable dilemma: allowlisted users cannot use the router (broken core functionality), and allowlisting the router to fix that opens a complete bypass for any non-allowlisted user.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the pool see `msg.sender = router`: [4](#0-3) 

The same applies to `exactInput` (L103-112), `exactOutputSingle` (L135-137), and `exactOutput` (L165-181) — in every case the pool receives `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]`, never `allowedSwapper[pool][originalUser]`. [5](#0-4) 

No existing guard recovers the original user's address; `extensionData` is passed through but the extension does not decode it, and there is no `originator` field in the swap interface. [6](#0-5) 

## Impact Explanation
Two fund-impacting outcomes follow directly. First, allowlisted users (e.g., KYC-verified addresses) calling any router function targeting an allowlisted pool will have their swap reverted with `NotAllowedToSwap`, making the primary periphery swap path entirely unusable — broken core swap functionality. Second, if the pool admin allowlists the router to restore access, `allowedSwapper[pool][router] = true` causes the extension to pass for every caller regardless of their individual allowlist status, completely defeating the curation policy. Non-allowlisted users can then trade on pools designed to exclude them, extracting value from LPs who deposited under the assumption that only vetted counterparties would trade. Both outcomes meet the "broken core pool functionality" and "admin-boundary bypass by unprivileged path" impact criteria. [7](#0-6) 

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entry point in `metric-periphery`. Any pool that deploys `SwapAllowlistExtension` will immediately encounter this mismatch the first time an allowlisted user calls `exactInputSingle` or `exactInput`. No special privileges are required: any ordinary user calling any router swap function on an allowlisted pool reproduces both failure modes. The bypass requires no flash loans, no price manipulation, and no privileged access — only a standard router call. [8](#0-7) 

## Recommendation
The pool must forward the original initiating user's address to extensions. Two approaches:

**Option A – Pass originator through `extensionData`.** The router encodes `msg.sender` into `extensionData` for each hop. `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of (or in addition to) `sender`. This requires a convention between router and extension but avoids interface changes.

**Option B – Add an `originator` field to the swap interface.** Extend `IMetricOmmPoolActions.swap` with an explicit `originator` parameter. The pool passes it to `_beforeSwap` alongside `sender`. Extensions gate on the true economic actor regardless of which intermediary called the pool.

Either way, `SwapAllowlistExtension` must check the address of the user who initiated the transaction, not the address of the contract that called `pool.swap()`. [6](#0-5) 

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwapOrder`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowed swapper.
3. Alice calls `router.exactInputSingle(...)` targeting that pool.
4. Inside `pool.swap()`, `msg.sender = router`. The extension checks `allowedSwapper[pool][router]` → `false` → reverts `NotAllowedToSwap`. Alice cannot trade through the router despite being allowlisted.
5. Pool admin calls `setAllowedToSwap(pool, router, true)` to unblock Alice.
6. Bob (not allowlisted) calls `router.exactInputSingle(...)`. The extension checks `allowedSwapper[pool][router]` → `true` → passes. Bob trades on the curated pool, bypassing the allowlist entirely.

Steps 3–4 are reproducible as a Foundry unit test by deploying the extension, configuring the pool, and asserting that `alice`'s router call reverts. Steps 5–6 are reproducible by then allowlisting the router and asserting that an unapproved address's router call succeeds. [9](#0-8)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
