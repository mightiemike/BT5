Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the direct `msg.sender` of `pool.swap()`. When users swap through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool admin who allowlists the router (required for any allowlisted user to reach the pool via the router) simultaneously opens the pool to all unprivileged users.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is whatever the pool forwarded as the swap initiator. In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` — the direct caller of `pool.swap()` — as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the originating user's address:

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
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

The result is an irreconcilable conflict: if the router is allowlisted (the only way for any allowlisted user to reach the pool through the router), every unprivileged user can bypass the guard by calling the router. If the router is not allowlisted, allowlisted users cannot use the router at all.

## Impact Explanation
Curated pools using `SwapAllowlistExtension` are designed to restrict trading to specific addresses (e.g., KYC-verified users, institutional counterparties). Once the router is allowlisted — a necessary step for any allowlisted user who wants to use the official periphery swap path — the allowlist is completely defeated. Any unprivileged user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router and trade on the curated pool without restriction. This constitutes an admin-boundary break: the pool admin's intended access policy is bypassed by an unprivileged path through a supported public contract.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the official periphery swap contract. Pool admins who deploy a curated pool with `SwapAllowlistExtension` will naturally need to allowlist the router so that their permitted users can access the standard swap flow. The moment they do so, the allowlist is open to all users. The trigger requires no special privilege, no malicious setup, and no non-standard token behavior — only a call to the public router.

## Recommendation
The extension must check the economically relevant actor — the end user — not the intermediary router. Two viable approaches:

1. **Extension-data forwarding**: The router encodes the originating `msg.sender` into `extensionData`; the extension decodes and checks that address. The pool admin must also configure the extension to trust the router as a forwarder.
2. **Trusted forwarder mapping**: Add a `trustedForwarder` mapping to the extension; when `sender` is a trusted forwarder, decode the real user from `extensionData` and check that address instead.

Until fixed, pool admins should be warned that `SwapAllowlistExtension` cannot enforce per-user access control when the router is allowlisted.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  extension.setAllowedToSwap(pool, alice, true)   // Alice is the only permitted swapper
  extension.setAllowedToSwap(pool, router, true)  // router allowlisted so Alice can use it

Attack:
  Bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})

Execution:
  router → pool.swap()          msg.sender in pool = router
  pool   → _beforeSwap(router, ...)
  pool   → extension.beforeSwap(sender=router, ...)
  extension checks allowedSwapper[pool][router] → true
  swap executes for Bob with no revert

Result: Bob successfully trades on a pool restricted to Alice, with no privileged access required.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
