Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, the router is that direct caller, so the extension checks whether the **router** is allowlisted rather than the originating user. Allowlisting the router (the only way to let allowlisted users trade through it) simultaneously grants every unprivileged user the ability to bypass the allowlist entirely.

## Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist at line 37 by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [1](#0-0) 

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // ← this becomes `sender` in the extension
  ...
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
``` [3](#0-2) 

The same pattern applies to `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`. For `exactInput`, intermediate hops use `address(this)` (the router itself) as the payer context, and the router is always `msg.sender` of every `pool.swap()` call: [4](#0-3) 

The result is a binary trap for the pool admin:

| Admin action | Effect |
|---|---|
| Does **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlists the router** | Every user can bypass the allowlist via the router |

There is no existing guard that recovers the original `msg.sender` of the router call. The router stores the original caller only in transient storage for the payment callback (`_setNextCallbackContext`), but this value is never forwarded to the extension as part of `extensionData` or any other channel. [5](#0-4) 

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC-verified counterparties, institutional partners, or whitelisted market makers loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and execute swaps against the pool. This is a complete admin-boundary break: an unprivileged path bypasses a configured guard, violating the pool's intended access policy and potentially exposing LP assets to unauthorized counterparties. The wrong value is `allowedSwapper[pool][router]` being evaluated instead of `allowedSwapper[pool][originalUser]`, causing the extension's access decision to be incorrect for every non-allowlisted user routing through the router.

## Likelihood Explanation

The bypass requires no special privileges, no flash loans, and no multi-transaction setup. Any user who can call the public router functions can exploit it. The only precondition is that the pool admin has allowlisted the router — a natural and expected action for any curated pool that intends to support standard periphery trading. The likelihood is high whenever such a pool is deployed.

## Recommendation

The extension must identify the economic actor (the end user), not the direct caller of `pool.swap()`. Two approaches:

1. **Pass the original user through the router via `extensionData`**: The router already stores the original `msg.sender` in transient storage for the payment callback. It should also encode this value into the `extensionData` it forwards to the pool, and `SwapAllowlistExtension` should decode and check it — but only when the caller is a trusted router.

2. **Check `recipient` instead of `sender`**: If the pool admin allowlists recipients rather than callers, the router cannot forge a different recipient without the user's cooperation, making the allowlist meaningful again.

The simplest safe interim fix is to document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and enforce this at the pool level (e.g., revert if `sender` is a known router address), or redesign the extension to decode the true originator from `extensionData` supplied by a trusted router.

## Proof of Concept

```solidity
// 1. Pool admin deploys a curated pool with SwapAllowlistExtension
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
address pool = factory.createPool(..., ext, ...);

// 2. Admin allowlists alice and the router (required for alice to use the router)
ext.setAllowedToSwap(pool, alice, true);
ext.setAllowedToSwap(pool, address(router), true); // ← necessary for alice to trade

// 3. Bob (not allowlisted) calls the router directly
// router.msg.sender = bob, but pool.swap msg.sender = router
// extension checks allowedSwapper[pool][router] == true → passes
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1e18,
    ...
})); // ← succeeds; allowlist bypassed for bob
```

The root cause is confirmed at `SwapAllowlistExtension.sol` line 37, which checks `sender` (the router, the direct pool caller) rather than the originating user. A Foundry integration test can reproduce this by deploying the extension and router against a live pool, allowlisting the router, and verifying that a non-allowlisted address successfully completes a swap via `exactInputSingle`.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```
