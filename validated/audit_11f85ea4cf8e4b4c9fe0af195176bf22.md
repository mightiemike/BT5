Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end-user, making the per-pool swap allowlist either permanently broken or trivially bypassable — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`MetricOmmPool.swap()` forwards its own `msg.sender` as the `sender` argument to `_beforeSwap`, so when users route through `MetricOmmSimpleRouter`, the pool sees `msg.sender = router`. `SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][router]` instead of the actual end-user. This produces two mutually exclusive failure modes: either every router-mediated swap reverts for allowlisted users (router not allowlisted), or any unprivileged address can bypass the allowlist entirely (router allowlisted).

## Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`, passing its own `msg.sender` as the `sender` argument:

```solidity
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` uses that `sender` argument as the identity to gate:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the original caller's identity:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap()` directly, making the pool see `msg.sender = router`. [4](#0-3) 

The pool admin has no third option: there is no mechanism in the router or the pool to thread the original caller's identity through to the extension. The `extensionData` field is user-supplied and unverified by the extension, so it cannot be used as a trusted identity channel without additional design.

## Impact Explanation

`SwapAllowlistExtension` is the sole on-chain mechanism for curated pools to restrict trading to a defined set of counterparties. Under **Mode B** (router allowlisted to unblock legitimate users), `allowedSwapper[pool][router] == true` causes the check to pass for every call through the router regardless of the actual caller — any unprivileged EOA can trade on a pool designed to be restricted, extracting value at oracle-derived prices that the LP set assuming only trusted counterparties would interact. Under **Mode A** (router not allowlisted), the standard periphery swap path is broken for all allowlisted users, making the pool's liquidity inaccessible through the supported entry point. Both outcomes constitute direct loss of LP assets or broken core pool functionality.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the documented, supported swap entry point for end-users. Any pool that deploys `SwapAllowlistExtension` and expects users to use the router will immediately encounter one of the two failure modes. No special timing, privileged access, or exotic token behavior is required — a single call to `exactInputSingle` from any EOA demonstrates the bypass. The precondition (pool with `SwapAllowlistExtension` configured) is a normal, intended deployment scenario.

## Recommendation

The extension must check the economically relevant actor, not the intermediary. Two sound approaches:

1. **Decode the real caller from `extensionData` with router as trusted source:** Have the router encode `msg.sender` into the `extensionData` field it forwards to the pool, and have the extension decode and verify that value only when `msg.sender` (the pool's caller, i.e., the router) is itself a trusted/allowlisted intermediary. The outer `msg.sender` check already in place in the extension can serve as the trust anchor for the router.

2. **Check `recipient` instead of `sender`:** If the intent is to gate who receives output tokens, `recipient` is the correct field and is already forwarded correctly through the router.

Either way, `sender` must not be used as the gated identity when the pool is expected to be called through an intermediary router.

## Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension in beforeSwap slot
  admin calls extension.setAllowedToSwap(pool, router, true)
    // admin must do this so legitimate users can use the router at all

Attack:
  attacker = any EOA not in the allowlist
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: attacker,
      ...
  })

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=attacker, ...) [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓ passes
      → swap executes at oracle price
      → attacker receives output tokens

Result:
  Non-allowlisted attacker completes a swap on a curated pool.
  The allowlist is completely bypassed.

Conversely, if admin does NOT allowlist the router:
  allowedSwapper[pool][router] == false → revert NotAllowedToSwap
  Every legitimate user who goes through the router is blocked.
```

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
