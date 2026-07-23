Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of end-user, allowing allowlist bypass - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's own `msg.sender` at swap time. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants every user access to the curated pool, completely nullifying the allowlist.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check at line 37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool (the extension's caller), and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap`, `_beforeSwap` is called with `msg.sender` as the first positional argument:

```solidity
_beforeSwap(
  msg.sender,   // ← pool's msg.sender, i.e. whoever called pool.swap()
  recipient,
  ...
);
```

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly without forwarding the original caller:

```solidity
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

The pool's `msg.sender` is the router, so `sender` forwarded to `beforeSwap` is the router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The same issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

The pool admin configures the allowlist with individual user addresses via `setAllowedToSwap(pool_, swapper, true)`, but the hook never sees those user addresses when swaps arrive through the router.

## Impact Explanation

Two fund-impacting outcomes arise:

1. **Allowlist bypass (high):** If the pool admin allowlists the router address (the natural step to enable router-mediated swaps for curated users), every user—including those explicitly excluded—can bypass the allowlist by calling any of the router's swap functions. The curated pool's access control is completely nullified, allowing unauthorized traders to execute swaps and extract value from the pool at oracle-derived prices.

2. **Broken core swap path (medium):** If the router is not allowlisted, individually allowlisted users cannot execute swaps through the router. Their only path is a direct `pool.swap` call, which requires implementing the `IMetricOmmSwapCallback` interface themselves. The supported periphery swap path is broken for the pool's intended users.

Both outcomes are reachable by any public caller without any privileged action beyond the pool admin's normal allowlist configuration.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint. Any curated pool that deploys `SwapAllowlistExtension` and expects users to swap through the router will encounter this mismatch. The pool admin has no on-chain signal that the identity being checked is the router rather than the user. The mismatch is structural and affects every router-mediated swap on every allowlisted pool.

## Recommendation

Pass the original end-user identity through the swap path. One approach: have the router encode the original `msg.sender` into `extensionData`, and have `beforeSwap` decode and check that address. Alternatively, the pool could accept an explicit `originator` argument that the router populates with `msg.sender` before calling `pool.swap`, forwarding it as the `sender` to extensions. The invariant that must hold is: the identity checked by `beforeSwap` must be the economically responsible actor who initiated the swap, not the intermediary contract that relayed it.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** allowlist `alice` (she is excluded).
4. Alice calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Router calls `pool.swap(...)` — pool's `msg.sender` is the router.
6. Pool calls `_beforeSwap(msg.sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Alice, who was explicitly excluded, successfully executes a swap on the curated pool.

Relevant code: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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
