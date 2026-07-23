Audit Report

## Title
Router-Mediated Swaps Bypass `SwapAllowlistExtension` Per-User Allowlist Due to Sender Identity Substitution — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a swap routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router address, not the end user. If the router is allowlisted for a pool, any unprivileged user can bypass the per-user allowlist by routing through the router.

## Finding Description

In `MetricOmmPool::swap`, the pool passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards this `sender` value unchanged to the extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension::beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` = pool, `sender` = whoever called `pool.swap()`. In `MetricOmmSimpleRouter::exactInputSingle`, the router calls `pool.swap()` with no mechanism to forward the original caller's identity — so `sender` = router address: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

When the admin allowlists the router (the natural configuration to enable router-mediated swaps for their users), `allowedSwapper[pool][router] = true`. Any unprivileged user calling `router.exactInputSingle(...)` causes the hook to see `sender = router` (allowlisted) and passes — the actual end user's address is never checked. The per-user allowlist is completely defeated.

## Impact Explanation

The `SwapAllowlistExtension` is an admin-boundary access control mechanism whose sole purpose is restricting swaps to specific actors (e.g., KYC-gated pools, institutional pools). Its bypass by any unprivileged user via the public router is a direct admin-boundary break. Pools relying on this extension for permissioned swap access have no effective protection against router-mediated swaps. This constitutes broken core pool functionality causing an admin-boundary break reachable by any unprivileged caller. Severity: High.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard public entrypoint for swaps. Any pool using `SwapAllowlistExtension` that also allowlists the router — a natural and expected configuration — is immediately vulnerable. No special timing, oracle manipulation, or multi-block sequencing is required. Any unprivileged address can exploit this by simply calling the public router.

## Recommendation

The `sender` passed to `beforeSwap` must represent the true originating user, not the intermediate caller. Concrete options:

1. **Router forwards original caller via `extensionData`** — encode the original `msg.sender` in `extensionData`; the hook reads it and validates that `msg.sender` (the pool's caller, i.e., the router) is a trusted router before accepting the forwarded identity.
2. **Separate router-allowlist from user-allowlist** — require both `allowedSwapper[pool][router]` AND a user-level check encoded in `extensionData`, so the router must attest to the caller.
3. **Pool-level trusted-router registry** — the pool verifies the caller is a trusted router and substitutes the forwarded caller identity before invoking extensions.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — intending to allow router-mediated swaps for allowlisted users only.
3. Unprivileged attacker (`0xDEAD`, not in allowlist: `allowedSwapper[pool][0xDEAD] == false`) calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` — `msg.sender` in pool = router.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Hook checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Attacker successfully swaps on a pool they were never allowlisted for.

Assert: `allowedSwapper[pool][0xDEAD] == false` yet the swap succeeds. The invariant "only allowlisted addresses may swap" is violated.

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
