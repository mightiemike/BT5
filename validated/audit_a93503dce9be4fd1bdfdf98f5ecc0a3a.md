Audit Report

## Title
`SwapAllowlistExtension` gates on the proximate caller (`msg.sender` of `pool.swap()`) rather than the originating user, enabling full allowlist bypass through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, producing two mutually exclusive failure modes: if the router is allowlisted, every user on the network bypasses the per-user allowlist; if the router is not allowlisted, every allowlisted user is silently blocked from using the router.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the first argument to every configured extension:

```solidity
// ExtensionCalling.sol L162-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then uses that first argument as the identity to gate:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

At the pool, `msg.sender = router`, so `sender = router`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Bypass path (router allowlisted):** A pool admin who wants to support router-mediated swaps must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the guard passes for every user regardless of their individual allowlist status. Any non-allowlisted user calls `router.exactInputSingle(pool, ...)` and the guard passes.

**Broken path (router not allowlisted):** If the pool admin allowlists individual EOAs but not the router, those EOAs cannot use the router at all — the extension reverts with `NotAllowedToSwap` because `allowedSwapper[pool][router] = false`.

No existing guard compensates for this: the extension has no mechanism to inspect `tx.origin` or any router-supplied attestation of the originating user. [6](#0-5) 

## Impact Explanation

**Allowlist bypass (High):** A curated pool (e.g., KYC-only, market-maker-only) that relies on `SwapAllowlistExtension` to restrict trading to approved counterparties is fully bypassed by any user routing through `MetricOmmSimpleRouter`. Non-approved users can execute swaps against the pool's oracle-anchored pricing, draining LP value or exploiting any pricing advantage the pool was designed to reserve for approved parties. This is a direct admin-boundary break reachable by any unprivileged user with no special preconditions beyond the router being allowlisted.

**Broken core swap functionality (Medium):** If the router is not allowlisted, allowlisted users cannot use the standard periphery swap path. All multi-hop routing (`exactInput`/`exactOutput`) and exact-output flows become inaccessible to the very users the pool was configured to serve, constituting broken core swap functionality.

## Likelihood Explanation

Any pool that deploys with `SwapAllowlistExtension` and expects users to route through `MetricOmmSimpleRouter` hits one of the two failure modes immediately upon first use. The router is the primary public swap entrypoint in the periphery. The misconfiguration is not hypothetical — it is the only stable operating point for a curated pool that also wants router support. No attacker capability beyond calling the public router is required.

## Recommendation

The extension must gate on the economic actor (the user who initiated the transaction), not the proximate caller of `pool.swap()`. Viable approaches:

1. **Pass the originating user through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and verifies it against a trusted-router registry. This requires the extension to maintain a set of trusted routers.
2. **Preferred — document incompatibility and enforce at pool creation:** Add a check in `validateExtensionsConfig` or in the router itself that rejects the combination of `SwapAllowlistExtension` on `beforeSwap` with router-mediated swaps, or provide a `RouterSwapAllowlistExtension` that the router calls before forwarding.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Non-allowlisted user `attacker` calls `router.exactInputSingle({pool: pool, recipient: attacker, ...})`.
4. Router calls `pool.swap(attacker, ...)` with `msg.sender = router`.
5. `beforeSwap` checks `allowedSwapper[pool][router] = true` → passes.
6. `attacker` successfully swaps on the curated pool despite never being individually allowlisted.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
  }
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
