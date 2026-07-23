Audit Report

## Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension::beforeSwap`, Breaking Per-User Allowlist Gating — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` the pool received. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. The hook therefore checks whether the router is allowlisted rather than the individual user, making per-user allowlist enforcement impossible when the router is used.

## Finding Description

`MetricOmmPool::swap` calls `_beforeSwap(msg.sender, ...)` at the start of every swap: [1](#0-0) 

`ExtensionCalling::_beforeSwap` encodes this `sender` value and forwards it verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension::beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` = pool, `sender` = router. The check is `allowedSwapper[pool][router]`, never `allowedSwapper[pool][originalUser]`.

`MetricOmmSimpleRouter::exactInputSingle` and `exactOutputSingle` call the pool directly, making the router the `msg.sender` to the pool in all swap entry points: [4](#0-3) [5](#0-4) 

## Impact Explanation

The pool admin faces an impossible choice. If the router is **not** allowlisted, every individually allowlisted user is blocked from using the router — core swap UX is broken. If the router **is** allowlisted (the expected production configuration to support normal UX), every address on the network can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`. The allowlist is completely defeated: any unprivileged address can swap on a pool intended to be restricted. This constitutes broken core pool functionality and an admin-boundary break where an access control intended to restrict swap participation is fully circumvented by an unprivileged caller.

## Likelihood Explanation

Any pool that uses `SwapAllowlistExtension` and allowlists the router to support normal user experience is fully bypassed. This is the expected production configuration — a pool that wants to restrict swappers but also support the official router must allowlist it, which opens the bypass to all users. No special privileges or conditions are required beyond calling the public `exactInputSingle` or `exactOutputSingle` entry points.

## Recommendation

The pool must receive the original user's address, not the router's. Options:
1. Pass the original caller address through `extensionData` and have the extension decode and verify it (with the router committing to it via a signature or transient storage slot).
2. Have the router store the original `msg.sender` in transient storage and expose a view that the extension reads during the hook call.
3. Require direct pool interaction (no router) for allowlisted pools — but this breaks UX.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for legitimate users.
3. Attacker (not individually allowlisted) calls `MetricOmmSimpleRouter::exactInputSingle` targeting the pool.
4. Pool calls `_beforeSwap(msg.sender=router, ...)`.
5. Extension checks `allowedSwapper[pool][router] == true` → passes.
6. Attacker's swap executes on a pool intended to be restricted.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
