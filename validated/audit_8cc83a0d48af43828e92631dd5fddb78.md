### Title
Swap Allowlist Guard Checks Router Address Instead of End User, Enabling Full Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. Any non-allowlisted user can bypass a curated pool's swap allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, passing its direct caller as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` to the pool is the **router contract**, so `sender` forwarded to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][end_user]`.

This creates an inescapable dilemma for the pool admin:

- **Router not allowlisted**: every router-mediated swap reverts for all users, breaking normal UX.
- **Router allowlisted**: the check degenerates to `allowedSwapper[pool][router] == true`, so **every user** — including those explicitly blocked — can swap by routing through the router.

The same structural flaw exists in the multi-hop path `exactInput` and the exact-output paths, all of which call `pool.swap()` with `msg.sender = router`: [5](#0-4) 

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to KYC'd or whitelisted counterparties. Any non-allowlisted user calls `router.exactInputSingle()` targeting the curated pool. Because the router is allowlisted (required for any router-mediated swap to work), the extension passes, and the non-allowlisted user executes a full swap against the pool. The allowlist guard is completely neutralized. This is a **High** severity finding: it is a direct, unprivileged bypass of a configured access-control guard on a production pool, with fund-impacting consequences (unauthorized trading on a curated pool, potential regulatory or LP-policy violations, and loss of the protection the pool admin paid to configure).

### Likelihood Explanation

Likelihood is **High**. The `MetricOmmSimpleRouter` is the standard, documented periphery swap entrypoint. Any user who knows the pool address can call it without any special privilege. No admin keys, no special tokens, no setup beyond a normal swap call are required.

### Recommendation

The extension must gate the **economically relevant actor** — the end user — not the intermediate contract. Two sound approaches:

1. **Pass the original initiator through the router**: Have the router encode `msg.sender` (the end user) in `extensionData` and have the extension decode and check that address. This requires a convention between the router and the extension.
2. **Check `tx.origin` as a fallback** (not recommended in general, but acceptable for allowlist-only extensions on non-contract callers): replace `sender` with `tx.origin` inside the extension. This is fragile for contract callers.
3. **Preferred — check `sender` as the router and require the router to forward the real user**: Redesign the router to pass the real initiator as the `recipient`-equivalent field in `extensionData`, and update the extension to decode it. This keeps the check inside the extension without changing the core pool interface.

The cleanest fix is option 1: the router encodes `msg.sender` into `extensionData` for allowlist-aware pools, and the extension decodes it when present, falling back to `sender` for direct pool calls.

### Proof of Concept

```
// Setup
pool = factory.createPool(..., extensions=[swapAllowlist], ...);
swapAllowlist.setAllowedToSwap(pool, allowedUser, true);
// router is allowlisted so router-mediated swaps work for allowedUser
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// Attack: blockedUser is NOT in the allowlist
vm.prank(blockedUser);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    recipient: blockedUser,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// ✓ swap succeeds — blockedUser bypassed the allowlist via the router
```

The extension checks `allowedSwapper[pool][router] == true` and passes. The blocked user receives output tokens from the curated pool. [6](#0-5) [7](#0-6)

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
