Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` inside `MetricOmmPool.swap`. When a swap is routed through `MetricOmmSimpleRouter`, that `msg.sender` is the router's contract address, not the actual user's address. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, any unprivileged user can bypass the allowlist entirely by calling the router directly.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- this is the router when called via router
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router stores the actual user's address only in transient storage for the payment callback, and calls `pool.swap(params.recipient, ...)` directly — the actual user's address is never surfaced to the pool or its extensions:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn); // user stored for payment only
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
```

So `msg.sender` inside `MetricOmmPool.swap` is the router, and `sender` arriving at the extension is the router address. The extension cannot distinguish between different users going through the same router instance.

This creates an inescapable binary for any pool admin who deploys a swap-allowlisted pool:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all |
| Router **allowlisted** | **Any** user can bypass the allowlist by calling the router |

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` because all of them call `pool.swap` with `msg.sender = router`.

## Impact Explanation

The swap allowlist is the primary access-control mechanism for restricted pools (KYC pools, private LP pools, partner-only markets). Once the router is allowlisted — which is required for any allowlisted user to use the standard periphery — the guard is completely neutralized for all router-mediated swaps. Any unprivileged address can execute swaps in a pool that was explicitly configured to block them, draining LP value through arbitrage or front-running in a pool whose LPs accepted risk only under the assumption that the allowlist was enforced. This constitutes a direct loss of LP assets and broken core pool access-control functionality, meeting the High severity threshold.

## Likelihood Explanation

High. The router is a public, permissionless contract. No special role, token, or setup is required beyond calling `exactInputSingle`. The pool admin is forced to allowlist the router to make the pool usable for legitimate users, at which point the bypass is unconditionally open to everyone. The attack requires zero capital beyond the swap input and is repeatable indefinitely.

## Recommendation

The actual initiating user must be threaded through the call chain so the extension can check it. Two viable approaches:

1. **Explicit `originator` parameter on `swap`**: Add an `address originator` field to the pool's `swap` signature (or to `extensionData`) that the router populates with `msg.sender` before calling the pool. The extension reads this field instead of (or in addition to) `sender`.
2. **Extension-data convention**: Define a standard ABI prefix in `extensionData` that the router always prepends with the real user address; the `SwapAllowlistExtension` decodes and verifies it, and also verifies that `sender` (the router) is a trusted forwarder registered with the factory.

## Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // Alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required so Alice can use the router

Attack
──────
4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:    <restricted pool>,
           ...
       })

5. Router calls pool.swap(recipient, ...) — msg.sender inside pool = router.

6. Pool calls _beforeSwap(router, recipient, ...).

7. Extension evaluates:
       allowedSwapper[pool][router]  →  true   ✓ (admin set this in step 3)
   → no revert

8. Bob's swap executes successfully despite never being allowlisted.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
