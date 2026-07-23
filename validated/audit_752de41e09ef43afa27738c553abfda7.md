Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End-User, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` at the pool boundary, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][end_user]`. Any pool admin who allowlists the router to support router-mediated swaps for legitimate users simultaneously grants every blocked user the ability to bypass the per-user restriction by calling the router instead of the pool directly.

## Finding Description
`MetricOmmPool.swap` captures `msg.sender` and forwards it as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap(), i.e. the router
  recipient,
  ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` passes that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is the router — not the end user.

`MetricOmmSimpleRouter.exactInputSingle` stores the original caller in transient storage for the payment callback but passes only `params.recipient` (not the original caller) to `pool.swap()`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn); // user stored for payment only
IMetricOmmPoolActions(params.pool).swap(params.recipient, ...);  // pool sees msg.sender = router
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The result is an inescapable dilemma: if the router is not allowlisted, all router-mediated swaps revert even for individually allowlisted users; if the router is allowlisted, every user — including explicitly blocked ones — can bypass the per-user gate by routing through the router.

## Impact Explanation
Any user explicitly blocked via `setAllowedToSwap(pool, user, false)` can bypass the restriction by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`, provided the router is allowlisted. The per-user allowlist — the sole access-control mechanism on the swap path for curated pools — is completely nullified for router-mediated swaps. Pools using this extension for KYC compliance, whitelist-only LP protection, or adversarial-flow prevention are fully exposed: disallowed counterparties can drain LP value through adverse selection or interact with pools never intended to be public. This constitutes a broken core pool access-control invariant with direct fund-loss potential for LPs.

## Likelihood Explanation
The bypass requires only that the router be allowlisted on the curated pool. Any pool admin who wants allowlisted users to access the pool via the primary user-facing swap interface (the router) is forced to allowlist the router — there is no alternative configuration. The trigger is a normal, unprivileged call to a public router function. No special privileges, flash loans, or unusual conditions are required.

## Recommendation
The extension must gate on the economically relevant actor — the end user — not the intermediary. Two approaches:

1. **Pass originator through the pool**: Have `MetricOmmPool.swap` record `msg.sender` at entry and forward it as a distinct `originator` field to extension hooks, separate from `sender`. Extensions gate on `originator`.
2. **Extension-data attestation**: Require the router to embed the original user's address in `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and verify it, with the pool or factory as the trusted attestor.

Until fixed, pool admins must be warned that `SwapAllowlistExtension` cannot enforce per-user restrictions for router-mediated swaps and must not allowlist the router on curated pools.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, bob, false)` — Bob is explicitly blocked.
4. Pool admin calls `setAllowedToSwap(pool, router, true)` — Router is allowlisted so Alice can use it.
5. Bob calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
6. Router calls `pool.swap(bob_recipient, ...)` with `msg.sender = router`.
7. Pool calls `_beforeSwap(router, bob_recipient, ...)` → extension receives `sender = router`.
8. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap succeeds.
9. Bob has bypassed the allowlist and swapped on the curated pool.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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
