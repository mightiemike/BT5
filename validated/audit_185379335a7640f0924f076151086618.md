Audit Report

## Title
Router-Mediated Swaps Bypass `SwapAllowlistExtension` Per-User Gate — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` always sets to its own `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks whether the **router** is allowlisted rather than the actual end-user. If the pool admin allowlists the router to support router-mediated swaps for legitimate users, every unprivileged user can bypass the per-user restriction by routing through the public router.

## Finding Description

`MetricOmmPool.swap()` unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool (`msg.sender` = pool): [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making itself `msg.sender` to the pool. The original user's address is stored only in transient storage via `_setNextCallbackContext` for the payment callback and is **never forwarded** to `pool.swap()` as `sender`: [4](#0-3) 

The same pattern applies to `exactOutputSingle` (L135–137), `exactInput` (L103–112), and `exactOutput` (L165–181) — all call `pool.swap()` with the router as `msg.sender`. [5](#0-4) 

The extension therefore sees `sender = router`, not `sender = actual_user`. No existing guard in the extension, pool, or router checks or forwards the original user identity.

## Impact Explanation

A pool configured as a restricted venue (e.g., only specific market-maker addresses may swap) is fully open to arbitrary swappers the moment the router is allowlisted. Those swappers can execute swaps at oracle-derived prices without any of the trust assumptions the pool admin intended to enforce. This constitutes a broken core pool access-control mechanism causing direct loss of LP value and violation of the admin-boundary invariant that `allowedSwapper[pool][user]` restricts swap access to only `user`.

## Likelihood Explanation

- `SwapAllowlistExtension` is a production extension explicitly designed to restrict swap access per user.
- Pool admins who want allowlisted users to use the router (the standard periphery entry point) have no choice but to allowlist the router address — this is a natural and expected operational step.
- `MetricOmmSimpleRouter` is a public, permissionless contract; any user can call `exactInputSingle` or any other `exact*` function.
- No privileged access, special token, or malicious setup is required — a standard `exactInputSingle` call suffices.

## Recommendation

The router must forward the original user's identity to the pool so the extension can gate the correct actor:

1. **Router-side**: Pass `msg.sender` (the original user) as the `sender` argument to `pool.swap()` via a new pool entry point or by encoding it in `extensionData` in a verifiable way.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should inspect an optional `extensionData` payload carrying the original user address (authenticated by a known router), and gate on that address rather than the raw `sender` when the immediate caller is a known router.

Until fixed, pool admins must **not** allowlist the router address; allowlisted users must call `pool.swap()` directly, implementing `IMetricOmmSwapCallback` themselves.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // alice is the intended grantee
  allowedSwapper[pool][router] = true   // admin adds router so alice can use it

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: charlie, ...})

  Execution trace:
    router.exactInputSingle()
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, charlie, tokenIn)
      → pool.swap(charlie_as_recipient, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (passes!)
        → swap executes, charlie receives output tokens

Result: charlie, who is not allowlisted, successfully swaps on a restricted pool
        by routing through the public MetricOmmSimpleRouter.
```

A Foundry test can reproduce this by deploying a pool with `SwapAllowlistExtension`, allowlisting only `alice` and the router, then calling `router.exactInputSingle` from an address that is not `alice` and asserting the swap succeeds.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-137)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
