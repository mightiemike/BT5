### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass a per-user allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If the pool admin allowlists the router (a necessary step to permit any router-mediated swap), the allowlist is silently bypassed for every user who routes through it, regardless of whether they are individually permitted.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is used, the router calls `pool.swap(...)`: [4](#0-3) 

So `sender` arriving at the extension is the **router address**, not the end user. The extension therefore checks `allowedSwapper[pool][router]`. The pool admin faces an inescapable dilemma:

- **Do not allowlist the router** → even individually-allowlisted users cannot use the router.
- **Allowlist the router** → every user on the network can bypass the per-user allowlist by routing through the public router contract.

There is no configuration that simultaneously permits allowlisted users to use the router while blocking non-allowlisted users.

### Impact Explanation

Any user who is **not** individually allowlisted can swap in a restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) once the pool admin has allowlisted the router. The allowlist guard — the sole access-control mechanism for swap gating — is rendered ineffective for all router-mediated paths. This is a direct admin-boundary break: a configured extension guard is bypassed by an unprivileged, publicly accessible path.

### Likelihood Explanation

Likelihood is **medium**. Any pool that (a) deploys `SwapAllowlistExtension` to restrict swappers and (b) also wants users to be able to use the standard router must allowlist the router. This is a natural operational step. Once the router is allowlisted, the bypass is immediately available to any address on-chain with no further preconditions.

### Recommendation

The pool's `swap()` signature does not carry an explicit "original caller" field, so the extension cannot recover the true end-user from `sender` alone when a router is involved. Two viable fixes:

1. **Encode the real swapper in `extensionData`**: Have the router append `msg.sender` to `extensionData` and have the extension decode and check that address when `sender` is a known router. This requires a coordinated protocol-level convention.
2. **Allowlist at the router level, not the pool level**: Gate access in the router itself (e.g., a separate allowlist checked before calling `pool.swap`) so the pool extension never needs to distinguish router from user.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension (BEFORE_SWAP_ORDER set)
  admin calls setAllowedToSwap(pool, router, true)   // to let users use the router
  admin calls setAllowedToSwap(pool, alice, true)    // alice is the only intended swapper
  bob is NOT allowlisted

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, ...)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap executes for bob, bypassing the per-user allowlist
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-41)
```text
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
