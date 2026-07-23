Audit Report

## Title
`SwapAllowlistExtension.beforeSwap()` checks router address as `sender` instead of originating user, allowing any router caller to bypass per-user swap allowlist — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract address. If the pool admin allowlists the router — a natural action to permit router-mediated swaps — every user of the router bypasses the per-user restriction, breaking the core invariant of the extension.

## Finding Description
`SwapAllowlistExtension.beforeSwap()` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`sender` is populated by `MetricOmmPool.swap()` as `msg.sender` of the `swap()` call, forwarded via `_beforeSwap(msg.sender, ...)`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // <-- direct caller of pool.swap()
    ...
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with `msg.sender = router`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The extension therefore receives `sender = router` and evaluates `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router address — a natural action to permit users to swap through the router — the check passes for every user who routes through `MetricOmmSimpleRouter`, regardless of whether that user is individually allowlisted. The same bypass applies to `exactInput` (multi-hop, line 104) and `exactOutputSingle` (line 136), which also call `pool.swap()` with `msg.sender = router`.

## Impact Explanation
The core invariant of `SwapAllowlistExtension` — "only allowlisted addresses can swap in this pool" — is broken. Any unprivileged user can bypass the per-user restriction by routing through `MetricOmmSimpleRouter`, a public contract callable by anyone. Unauthorized users can execute swaps in a pool intended to be access-controlled, causing unauthorized price impact, extracting value from the pool at favorable oracle-derived prices, or violating the pool's intended access policy. This is a direct breach of the allowlist guard with fund-impacting consequences, matching the "Admin-boundary break" and "broken core pool functionality causing loss of funds" allowed impact categories.

## Likelihood Explanation
The bypass requires the pool admin to allowlist the router. This is a natural and expected action: an admin who wants to restrict swaps to specific users but also wants those users to be able to use the router would allowlist the router address. The admin may not realize that allowlisting the router effectively opens the pool to all users of that router. `MetricOmmSimpleRouter` is a public contract callable by anyone. The precondition (router allowlisted) is a foreseeable and common configuration, making this a realistic exploit path.

## Recommendation
`SwapAllowlistExtension` should check the original user's address, not the intermediary router's address. One approach: require the router to encode the original user's address in `extensionData`, and have the extension decode and check it. A simpler approach: document clearly that allowlisting the router grants access to all router users, and provide a separate "user-forwarding" extension variant that reads the originating user from `extensionData`. The `DepositAllowlistExtension` has a symmetric design choice (checking `owner` rather than `sender`) that should be reviewed for the same class of mismatch.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` extension.
2. Pool admin calls `setAllowedToSwap(pool, allowedUser, true)` to allowlist a specific user.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps for that user.
4. Unauthorized user (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle()` targeting the restricted pool.
5. Router calls `pool.swap()` with `msg.sender = router`.
6. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` evaluates `allowedSwapper[pool][router] = true`.
7. The swap proceeds — the unauthorized user has bypassed the per-user restriction entirely.

**Foundry test sketch:**
```solidity
// 1. Deploy pool with SwapAllowlistExtension as beforeSwap hook
// 2. allowedSwapper[pool][allowedUser] = true
// 3. allowedSwapper[pool][router] = true
// 4. vm.prank(unauthorizedUser); router.exactInputSingle(...)
// 5. Assert swap succeeds (no NotAllowedToSwap revert)
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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
